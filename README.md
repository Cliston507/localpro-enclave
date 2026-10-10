# LocalPro Enclave

A self-hosted MCP server that gives Alexa+ the skills of a home-services dispatcher. It finds a license-verified local pro, quotes the job, books it, and issues a signed work order that the technician must present on arrival.

Built for the [Build, Ship, Shape: Amazon Developer Hackathon](https://amazonappdev2026.devpost.com/), Alexa+ track.

- **Protocol:** MCP spec 2025-11-25 over Streamable HTTP, at `POST /mcp`
- **SDK:** official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (`mcp==2.3.0`)
- **Entry point:** `localpro/server.py`, run with `python -m localpro.server`
- **Latency:** every tool call takes under 50 ms locally (Alexa+ requires under 500 ms)
- **Demo:** a browser-based Alexa+ simulator at `/` that acts as a real MCP client

## The problem

When a pipe bursts at 2 AM, people want to say "I need a plumber now" and get someone trustworthy on the way, not scroll through listings. Small trade businesses have the opposite problem: slow phone tag, plus fake or disputed work orders. LocalPro Enclave handles both in one conversation and makes every booking verifiable.

## Tools

| Tool | What it does |
|---|---|
| `find_pros` | Finds license-verified contractors for a trade and city, with each pro's next open arrival window. Unlicensed contractors are always excluded. Emergencies are sorted by soonest arrival. |
| `quote_job` | Returns a price range, a call-out fee, and an arrival window held for 30 minutes. Emergency jobs are priced higher and can use after-hours slots with 24/7 pros. |
| `book_job` | Books a quote once, prevents double-booking, creates a work order, and returns a signed authorization token. |
| `verify_work_order` | Checks a token's signature, expiry, and contractor. With `check_in=true` it records arrival, and the token can't be used again. |

The server also sends Alexa+ instructions: always confirm with the customer before calling `book_job`.

### Example conversation

```
Customer: "Alexa, my water heater is leaking. I need a plumber today in Lincoln City."
  find_pros  -> Tidewater Plumbing, rated 4.9, arriving Today 11:00 AM to 1:00 PM (1 unlicensed pro filtered out)
  quote_job  -> $225 to $675 plus a $134 call-out fee, held 30 minutes
Customer: "Yes, book it. It's for Sam at 1200 SW Ocean Drive."
  book_job   -> Work order WO-E7495CF8 + signed token
Technician arrives:
  verify_work_order (wrong contractor)  -> rejected
  verify_work_order (tampered token)    -> rejected
  verify_work_order (booked pro)        -> verified, checked in
  verify_work_order (same token again)  -> rejected, single-use
```

## Quick start

Requires Python 3.10 or later.

```bash
git clone https://github.com/Cliston507/localpro-enclave.git
cd localpro-enclave
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

export LOCALPRO_SIGNING_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m localpro.server          # serves http://127.0.0.1:8000/mcp
```

Open **http://127.0.0.1:8000/** for the Alexa+ simulator, served by the same process.

### Alexa+ simulator

Entrants can't connect a self-hosted MCP server to a real Alexa+ device, so this repo follows the hackathon's simulated-experience path. The page in `localpro/web/` is a real MCP client: it sends `initialize`, `tools/list`, and `tools/call` as JSON-RPC 2.0 over Streamable HTTP to `/mcp`, and handles session IDs and event-stream responses.

- **Conversation:** type, tap a suggestion, or use the mic (Chrome). Replies are read aloud with the browser's speech engine. A small scripted dialogue manager stands in for Alexa+'s language model, and every fact it says comes from a tool result.
- **Technician check-in:** after booking, test the signed work order four ways: the booked pro checks in, a wrong contractor, a tampered token, and reuse after check-in.
- **Live MCP traffic:** every JSON-RPC request and response, with round-trip time.

### Scripted terminal demo

In a second terminal, run the scripted demo. It plays the part of Alexa+ and calls every tool over Streamable HTTP:

```bash
python scripts/demo.py
```

### Inspect with MCP Inspector

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP, URL: http://127.0.0.1:8000/mcp
```

### Raw JSON-RPC check

```bash
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

## Tests

```bash
pytest -q
```

There are 16 tests. Fourteen cover the dispatch logic: licensing filter, pricing, after-hours rules, double-booking, quote expiry and reuse, and token tampering, expiry, wrong contractor, and reuse. Two end-to-end tests start the real server: one negotiates protocol 2025-11-25 over HTTP and runs the full flow, and one checks that the simulator page is served.

## Security model

- **Signed work orders.** Tokens are `payload.signature`, where the signature is HMAC-SHA256 with a server-only secret (`LOCALPRO_SIGNING_KEY`). Any change to the payload breaks the signature, and signatures are compared in constant time.
- **Bound to one contractor.** A token only verifies for the pro it was issued to.
- **Single-use and short-lived.** Check-in consumes the token, and it expires 4 hours after the arrival window ends. Each token also carries a random nonce stored with the work order.
- **Verified pros only.** Unlicensed contractors are never returned, quoted, or booked.
- **No secrets in the repo.** The signing key comes from the environment. If it isn't set, a random key is generated at startup, so tokens won't survive a restart.
- **DNS-rebinding protection.** This is on by default when bound to localhost. Set `LOCALPRO_ALLOWED_HOSTS` when serving through a tunnel.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LOCALPRO_SIGNING_KEY` | random per run | Secret for signing work orders, at least 32 bytes |
| `LOCALPRO_DB` | `localpro.db` | SQLite file for quotes and work orders |
| `LOCALPRO_PROS` | bundled sample data | Path to a custom contractor JSON file |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address |
| `LOCALPRO_ALLOWED_HOSTS` | none | Comma-separated public hostnames, for example a cloudflared tunnel |
| `LOCALPRO_CORS_ORIGINS` | none | Only if the simulator is hosted on a different origin than the server |

### Exposing a public URL for testing

```bash
cloudflared tunnel --url http://localhost:8000
export LOCALPRO_ALLOWED_HOSTS=<your-subdomain>.trycloudflare.com
python -m localpro.server
```

### Deploy to Render (free)

The repo includes a `render.yaml` blueprint, so the server and the simulator page go online together.

1. Sign in at [render.com](https://render.com) with GitHub.
2. Choose **New**, then **Blueprint**, and pick this repository.
3. Click **Apply**. Render installs the package, generates a signing key, and starts the server.
4. Open the `onrender.com` address Render shows. The simulator page loads at `/`, and the MCP endpoint is at `/mcp`.

On the free plan the service sleeps after 15 minutes without traffic and takes about a minute to wake up. The database resets whenever it restarts, which is fine for a demo ([Render docs](https://render.com/docs/free)). Open the page once before a demo or judging session to wake it.

## Project layout

```
localpro/
  server.py       MCP server: tool definitions, Streamable HTTP entry point, simulator routes
  dispatch.py     Core logic: matching, availability, pricing, booking, verification
  tokens.py       HMAC-SHA256 work-order tokens
  data/pros.json  Sample contractors (fictional) on the Oregon coast
  web/            Alexa+ simulator: index.html, app.js (MCP client + dialogue), app.css
scripts/demo.py   Scripted Alexa+ conversation over Streamable HTTP
tests/            Unit tests and an end-to-end HTTP test
```

## Sample data

All contractors, license numbers, and prices are fictional and for demonstration only.

## What's next

- Echo Show cards with a live map and the technician's arrival time
- Real contractor onboarding with license and insurance checks
- Voice confirmation for high-value jobs before payment

## License

MIT. See [LICENSE](LICENSE).
