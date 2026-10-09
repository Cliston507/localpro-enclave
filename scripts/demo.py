"""Scripted demo: plays the part of Alexa+ and calls the LocalPro Enclave MCP server over Streamable HTTP.

Usage:
    python -m localpro.server            # terminal 1
    python scripts/demo.py               # terminal 2 (or pass a URL)
"""

from __future__ import annotations

import sys
import time

import anyio

from mcp import Client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"


async def timed(client: Client, name: str, args: dict):
    start = time.perf_counter()
    result = await client.call_tool(name, args)
    ms = (time.perf_counter() - start) * 1000
    if result.is_error:
        print(f"  [{name}] error after {ms:.0f} ms: {result.content[0].text}")
        return None, ms
    print(f"  [{name}] {ms:.0f} ms")
    return result.structured_content, ms


async def main() -> None:
    timings = []
    async with Client(URL, mode="legacy") as c:
        print(f"Connected to {c.server_info.name if c.server_info else 'server'} (MCP {c.protocol_version})\n")
        print('Customer: "Alexa, my water heater is leaking. I need a plumber today in Lincoln City."\n')

        found, ms = await timed(c, "find_pros", {"trade": "plumber", "city": "Lincoln City", "urgency": "emergency"})
        timings.append(ms)
        top = found["pros"][0]
        print(f"  Alexa: The soonest licensed plumber is {top['name']}, rated {top['rating']}, "
              f"arriving {top['next_arrival_window']}.")
        print(f"         ({found['excluded_unlicensed']} unlicensed contractor(s) were filtered out.)\n")

        quote, ms = await timed(c, "quote_job", {"pro_id": top["pro_id"], "issue": "water heater leaking", "urgency": "emergency"})
        timings.append(ms)
        print(f"  Alexa: {quote['message']} Should I book it?\n")
        print('Customer: "Yes, book it. It\'s for Sam at 1200 SW Ocean Drive."\n')

        booked, ms = await timed(c, "book_job", {"quote_id": quote["quote_id"], "customer_name": "Sam",
                                                 "service_address": "1200 SW Ocean Dr, Lincoln City, OR"})
        timings.append(ms)
        print(f"  Alexa: {booked['message']}\n")
        token = booked["authorization_token"]

        print("Technician arrives and scans the work order...\n")
        imposter, ms = await timed(c, "verify_work_order", {"authorization_token": token, "pro_id": "pro-102"})
        timings.append(ms)
        print(f"  Wrong contractor -> verified={imposter['verified']}: {imposter['reason']}")
        tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
        bad, ms = await timed(c, "verify_work_order", {"authorization_token": tampered, "pro_id": top["pro_id"]})
        timings.append(ms)
        print(f"  Tampered token  -> verified={bad['verified']}: {bad['reason']}")
        ok, ms = await timed(c, "verify_work_order", {"authorization_token": token, "pro_id": top["pro_id"], "check_in": True})
        timings.append(ms)
        print(f"  Booked pro      -> verified={ok['verified']}, checked_in={ok['checked_in']}")
        again, ms = await timed(c, "verify_work_order", {"authorization_token": token, "pro_id": top["pro_id"], "check_in": True})
        timings.append(ms)
        print(f"  Reused token    -> verified={again['verified']}: {again['reason']}\n")

    print(f"Tool calls: {len(timings)}, max round trip {max(timings):.0f} ms, "
          f"average {sum(timings) / len(timings):.0f} ms (Alexa+ requires under 500 ms).")


if __name__ == "__main__":
    anyio.run(main)
