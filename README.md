# homelab-soc

A real-time security operations centre running on a Raspberry Pi 4 — MCP servers for Tailscale and Microsoft Graph, a live web dashboard, push alerting, and Tailscale as the networking backbone.

Written up in full here: [Building a Homelab SOC on a Raspberry Pi with MCP and Microsoft Graph](https://cohenholmes.co.uk/blog/homelab-soc-raspberry-pi).

> **Status:** the write-up describes a working v1. The source in this repo is being cleaned up for others to actually run before it lands here — this README exists so the link in the blog post has somewhere real to point to. Watch this space.

## Architecture

Three layers, all running via Docker Compose on the Pi:

```
MacBook Pro (Claude Desktop)
  └── npx mcp-remote → Pi:8080  (mcp-tailscale)
  └── npx mcp-remote → Pi:8090  (mcp-graph)

Raspberry Pi 4 (on the tailnet)
  └── mcp-tailscale  :8080  — Tailscale API + shell
  └── mcp-graph      :8090  — Microsoft Graph tools
  └── dashboard      :8081  — Web UI
  └── ntfy           :8082  — Push notifications
  └── alerter              — Polling + alerts
```

- **The Pi** runs everything. `mcp-tailscale` talks to the Tailscale API and exposes a `run_command` tool for shell access inside the container. `mcp-graph` connects to Microsoft Graph using a registered Entra app with the client credentials flow. A Starlette-based dashboard renders the web UI with no JS framework and no build step. A separate alerter container polls both sources and fires push notifications via a self-hosted [ntfy](https://ntfy.sh) instance.
- **Tailscale** is the backbone — every service is only reachable over the tailnet, no ports exposed to the internet.
- **Claude Desktop** connects to both MCP servers via `mcp-remote`, proxied over Tailscale, giving Claude live tools (`list_devices`, `get_risky_sign_ins`, `list_ca_policies`, `check_mfa_gaps`) without copying and pasting API responses.

## Stack

- Python, [FastMCP](https://github.com/jlowin/fastmcp) for the MCP protocol layer
- Docker Compose for orchestration
- Starlette for the dashboard (server-rendered HTML, inline CSS, no build step)
- Tailscale for networking
- Microsoft Graph (client credentials flow) for Entra/security data
- ntfy for push notifications

## What it does

- **Overview** — stat cards and a device chart, pulling from Tailscale and Graph in parallel
- **Sign-ins** — recent failures, success/failure breakdown
- **Devices** — full Tailscale device list
- **Entra audit** — Conditional Access policy state, MFA status
- **Security posture** — a composite score: 100 points, minus 10 per user without MFA registered, minus 20 if a CA policy changes state unexpectedly

The alerter checks three things on independent schedules: a sign-in spike (>10 failures/hour, checked every 5 minutes), any Conditional Access policy changing state (checked every 10 minutes, since CA policies don't change on their own), and new enabled accounts without MFA registered (checked every 15 minutes). Each check is stateful — it only alerts on a delta, not on every poll.

## What's next

- Stale account detection — users inactive for 90+ days, reported weekly
- Named location drift — alert if a sign-in succeeds from an IP not in any named location
- Replace polling with Microsoft Graph webhook subscriptions for sign-in events, cutting alert latency from minutes to seconds (the real blocker here: Graph can't push a notification to a private Tailscale address, so this needs a public-facing receiver — a genuine architecture change from the current fully-private setup)

## License

TBD — added once the source itself is published here.
