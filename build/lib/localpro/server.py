"""LocalPro Enclave MCP server.

Exposes four tools over MCP Streamable HTTP (spec 2025-11-25) so Alexa+ or any
MCP client can find, quote, book, and verify local home-service jobs.

Run:  python -m localpro.server        (serves http://127.0.0.1:8000/mcp)
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from .dispatch import Dispatcher, DispatchError
from .tokens import Signer

log = logging.getLogger("localpro")

WEB_DIR = Path(__file__).resolve().parent / "web"
WEB_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}

INSTRUCTIONS = """\
LocalPro Enclave is a home-services dispatcher for the Oregon coast.
Typical flow for a customer request such as "my water heater is leaking, I need someone today in Lincoln City":
1. find_pros with the trade, city, and urgency.
2. quote_job for the pro the customer picks (default to the first result). Read the arrival window and price aloud.
3. Only after the customer clearly says yes, call book_job with the quote_id, their name, and service address.
4. Tell the customer the work order number. The technician uses verify_work_order on arrival.
Never book without explicit confirmation. Only license-verified pros are ever returned.
"""


def build_dispatcher() -> Dispatcher:
    secret_env = os.environ.get("LOCALPRO_SIGNING_KEY")
    if secret_env:
        secret = secret_env.encode("utf-8")
    else:
        secret = secrets.token_bytes(32)
        log.warning("LOCALPRO_SIGNING_KEY not set; using a random key. Tokens will not survive a restart.")
    db_path = os.environ.get("LOCALPRO_DB", "localpro.db")
    pros_path = os.environ.get("LOCALPRO_PROS")  # optional custom contractor list
    from .dispatch import load_pros

    return Dispatcher(db_path=db_path, signer=Signer(secret), pros=load_pros(pros_path))


def create_server(dispatcher: Dispatcher | None = None) -> MCPServer:
    d = dispatcher or build_dispatcher()
    mcp = MCPServer(
        name="localpro-enclave",
        title="LocalPro Enclave",
        description="Find, quote, book, and verify trusted local home-service pros.",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    def run(fn, *args, **kwargs) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except DispatchError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(
        title="Find local pros",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    def find_pros(
        trade: Annotated[str, Field(description="Trade needed: plumbing, electrical, roofing, hvac, or handyman. Plain words like 'plumber' or 'furnace' also work.")],
        city: Annotated[str, Field(description="City where the work is needed, for example 'Lincoln City'.")],
        urgency: Annotated[Literal["emergency", "standard"], Field(description="'emergency' for same-day or after-hours problems, otherwise 'standard'.")] = "standard",
        limit: Annotated[int, Field(ge=1, le=5, description="Maximum number of pros to return.")] = 3,
    ) -> dict[str, Any]:
        """Find license-verified contractors who serve a city, with each pro's next open arrival window.

        Unlicensed contractors are always excluded. For emergencies, results are ordered by soonest arrival.
        """
        return run(d.find_pros, trade, city, urgency, limit)

    @mcp.tool(
        title="Quote a job",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
    )
    def quote_job(
        pro_id: Annotated[str, Field(description="pro_id from find_pros, for example 'pro-101'.")],
        issue: Annotated[str, Field(description="Short description of the problem, for example 'water heater leaking'.")],
        urgency: Annotated[Literal["emergency", "standard"], Field(description="Same urgency used in find_pros.")] = "standard",
    ) -> dict[str, Any]:
        """Get a price range and a held arrival window from one pro. The quote is valid for 30 minutes.

        Read the arrival window and estimate to the customer and ask for confirmation before booking.
        """
        return run(d.quote_job, pro_id, issue, urgency)

    @mcp.tool(
        title="Book a job",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
    )
    def book_job(
        quote_id: Annotated[str, Field(description="quote_id from quote_job.")],
        customer_name: Annotated[str, Field(description="Customer's name for the work order.")],
        service_address: Annotated[str, Field(description="Street address where the work will be done.")],
    ) -> dict[str, Any]:
        """Book the quoted job and create a work order with a signed authorization token.

        Only call this after the customer explicitly confirms. Each quote can be booked once.
        """
        return run(d.book_job, quote_id, customer_name, service_address)

    @mcp.tool(
        title="Verify a work order",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
    )
    def verify_work_order(
        authorization_token: Annotated[str, Field(description="authorization_token from book_job, presented by the technician.")],
        pro_id: Annotated[str, Field(description="pro_id of the contractor presenting the token.")],
        check_in: Annotated[bool, Field(description="True to record arrival. A token can be checked in only once.")] = False,
    ) -> dict[str, Any]:
        """Check that a work order token is authentic, unexpired, and issued to this contractor.

        Rejects tampered, expired, reused, or wrong-contractor tokens. With check_in=True, records arrival.
        """
        return run(d.verify_work_order, authorization_token, pro_id, check_in)

    # Alexa+ simulator web page (an MCP client in the browser), served alongside /mcp.
    for route_path, (filename, media_type) in WEB_FILES.items():
        def make_handler(fname: str = filename, mtype: str = media_type):
            async def handler(request: Request) -> Response:
                path = WEB_DIR / fname
                if not path.is_file():
                    return Response("Simulator files not found.", status_code=404)
                return FileResponse(path, media_type=mtype, headers={"Cache-Control": "no-cache"})
            return handler

        mcp.custom_route(route_path, methods=["GET"])(make_handler())

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok", "tools": 4})

    return mcp


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    allowed = [h.strip() for h in os.environ.get("LOCALPRO_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = None
    if allowed:
        # Needed when serving through a tunnel or public hostname.
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed + [f"127.0.0.1:{port}", f"localhost:{port}"],
            allowed_origins=[f"https://{h}" for h in allowed],
        )
    import uvicorn

    app = create_server().streamable_http_app(streamable_http_path="/mcp", transport_security=security, host=host)
    cors = [o.strip() for o in os.environ.get("LOCALPRO_CORS_ORIGINS", "").split(",") if o.strip()]
    if cors:
        # Only for hosting the simulator on a different origin than the server.
        from starlette.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware, allow_origins=cors, allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"], expose_headers=["Mcp-Session-Id"],
        )
    log.info("Simulator: http://%s:%d/   MCP endpoint: http://%s:%d/mcp", host, port, host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
