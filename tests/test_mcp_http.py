"""End-to-end test: run the real server over Streamable HTTP and drive it with an MCP client."""

import os
import socket
import subprocess
import sys
import time

import anyio
import pytest

from mcp import Client


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    port = _free_port()
    env = dict(os.environ, PORT=str(port), LOCALPRO_SIGNING_KEY="t" * 40,
               LOCALPRO_DB=str(tmp_path_factory.mktemp("db") / "e2e.db"))
    proc = subprocess.Popen([sys.executable, "-m", "localpro.server"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start:\n" + proc.stdout.read().decode())
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def _data(result):
    assert not result.is_error, result.content
    return result.structured_content


def test_handshake_and_full_flow(server_url):
    async def run():
        async with Client(server_url, mode="legacy") as c:
            assert c.protocol_version == "2025-11-25"
            names = {t.name for t in (await c.list_tools()).tools}
            assert names == {"find_pros", "quote_job", "book_job", "verify_work_order"}

            found = _data(await c.call_tool("find_pros", {"trade": "plumber", "city": "Lincoln City", "urgency": "emergency"}))
            pro_id = found["pros"][0]["pro_id"]
            quote = _data(await c.call_tool("quote_job", {"pro_id": pro_id, "issue": "water heater leaking", "urgency": "emergency"}))
            booked = _data(await c.call_tool("book_job", {"quote_id": quote["quote_id"], "customer_name": "Demo",
                                                          "service_address": "1 Ocean Dr, Lincoln City"}))
            token = booked["authorization_token"]

            wrong = _data(await c.call_tool("verify_work_order", {"authorization_token": token, "pro_id": "pro-999"}))
            assert wrong["verified"] is False
            ok = _data(await c.call_tool("verify_work_order", {"authorization_token": token, "pro_id": pro_id, "check_in": True}))
            assert ok["verified"] and ok["checked_in"]
            reuse = _data(await c.call_tool("verify_work_order", {"authorization_token": token, "pro_id": pro_id, "check_in": True}))
            assert reuse["verified"] is False

            err = await c.call_tool("quote_job", {"pro_id": "pro-502", "issue": "leak"})
            assert err.is_error and "license" in err.content[0].text

    anyio.run(run)
