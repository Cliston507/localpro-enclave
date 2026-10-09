# LocalPro Enclave

A self-hosted MCP server that gives Alexa+ the skills of a home-services dispatcher. It finds a license-verified local pro, quotes the job, books it, and issues a signed work order that the technician must present on arrival.

Built for the [Build, Ship, Shape: Amazon Developer Hackathon](https://amazonappdev2026.devpost.com/), Alexa+ track.

- **Protocol:** MCP spec 2025-11-25 over Streamable HTTP, at `POST /mcp`
- **SDK:** official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (`mcp==2.3.0`)
- **Entry point:** `localpro/server.py`, run with `python -m localpro.server`
- **Latency:** every tool call takes under 50 ms locally (Alexa+ requires under 500 ms)

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

There are 15 tests. Fourteen cover the dispatch logic: licensing filter, pricing, after-hours rules, double-booking, quote expiry and reuse, and token tampering, expiry, wrong contractor, and reuse. One end-to-end test starts the real server, negotiates protocol 2025-11-25 over HTTP, and runs the full flow.

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

### Exposing a public URL for testing

```bash
cloudflared tunnel --url http://localhost:8000
export LOCALPRO_ALLOWED_HOSTS=<your-subdomain>.trycloudflare.com
python -m localpro.server
```

## Project layout

```
localpro/
  server.py       MCP server: tool definitions and Streamable HTTP entry point
  dispatch.py     Core logic: matching, availability, pricing, booking, verification
  tokens.py       HMAC-SHA256 work-order tokens
  data/pros.json  Sample contractors (fictional) on the Oregon coast
scripts/demo.py   Scripted Alexa+ conversation over Streamable HTTP
tests/            Unit tests and an end-to-end HTTP test
```

## Sample data

All contractors, license numbers, and prices are fictional and for demonstration only.

## What's next

- Web simulator: a browser Alexa+ conversation that calls this server live
- Echo Show cards with a live map and the technician's arrival time
- Real contractor onboarding with license and insurance checks
- Voice confirmation for high-value jobs before payment

## License

MIT. See [LICENSE](LICENSE).
