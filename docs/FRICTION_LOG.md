# Friction Log

Entries follow the hackathon's friction log format: task, steps, expected versus actual result, severity, workaround, and an actionable suggestion. Severity: low (annoying), medium (cost real time), high (blocked progress).

## 1. Connecting a self-hosted MCP server to a real Alexa+ device

- **Task:** Test LocalPro Enclave's four tools from a real Alexa+ device.
- **Steps:** Read the Alexa+ MCP Toolkit QuickStart, then searched the hackathon resources and FAQ for how entrants register a custom MCP endpoint.
- **Expected:** A developer console flow for registering a Streamable HTTP endpoint and testing it on an Echo device.
- **Actual:** The hackathon FAQ says device and console connection is limited to select partners. Entrants submit a simulated experience instead.
- **Severity:** Medium. The server can't be validated against the real Alexa+ runtime, including its latency budget and how it selects tools.
- **Workaround:** Built a browser-based simulator that acts as a real MCP client (`initialize`, `tools/list`, `tools/call` over Streamable HTTP, protocol 2025-11-25) and shows every JSON-RPC message.
- **Suggestion:** Offer entrants a hosted test harness or sandbox skill that calls a submitted MCP URL the way Alexa+ does, with recorded latency and tool-selection logs, even if it isn't on a real device.

## 2. MCP Python SDK 2.x renamed FastMCP

- **Task:** Write the server with the official MCP Python SDK.
- **Steps:** Installed the current `mcp` package (2.3.0) and followed common examples that use `from mcp.server.fastmcp import FastMCP`.
- **Expected:** The example import works.
- **Actual:** `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. In 2.x, FastMCP is `MCPServer` in `mcp.server.mcpserver`, and other APIs changed. The error message links to the migration guide.
- **Severity:** Low. The error message gave the fix, though older examples needed rework.
- **Workaround:** Used `from mcp.server.mcpserver import MCPServer` and pinned `mcp==2.3.0` in `pyproject.toml`.
- **Suggestion:** Pin an SDK version in Alexa+ MCP samples and note the 1.x to 2.x rename, since most tutorials online still use the 1.x import.

## 3. DNS-rebinding protection rejects tunnel hostnames

- **Task:** Expose the local server through a tunnel so an external client can reach it.
- **Steps:** Ran the server on `127.0.0.1:8000` and sent an `initialize` request with `Host: abc.trycloudflare.com`.
- **Expected:** A normal `initialize` response.
- **Actual:** `HTTP 421 Invalid Host header`. The SDK turns on host checking automatically when bound to localhost.
- **Severity:** Medium. A 421 error with a one-line body is hard to trace back to a security setting.
- **Workaround:** Added a `LOCALPRO_ALLOWED_HOSTS` setting that passes the tunnel hostname to `TransportSecuritySettings`. For cloud hosting, binding to `0.0.0.0` behind the platform's proxy avoids the check.
- **Suggestion:** Add a "testing through a tunnel" step to the Alexa+ MCP QuickStart that shows the allowed-hosts setting. Include the allowed hosts in the SDK's 421 error message.

## 4. Two collaborator lists in the official rules

- **Task:** Confirm which Amazon GitHub accounts need access if the repo is private.
- **Steps:** Read the repository requirements in the official rules.
- **Expected:** One list of usernames.
- **Actual:** The rules name five Amazon Developer Relations users, then a second list that adds `chris-trag`.
- **Severity:** Low. The repo is public, so this doesn't apply here, but private-repo entrants could miss a judge.
- **Workaround:** Made the repo public with an MIT license, which the rules accept.
- **Suggestion:** Publish a single list of usernames in the rules and the FAQ.
