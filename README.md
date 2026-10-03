# homelab-soc

A real-time security operations centre running on a Raspberry Pi 4 — MCP servers for Tailscale and Microsoft Graph, a live web dashboard, push alerting, and Tailscale as the networking backbone.

Written up in full here: [Building a Homelab SOC on a Raspberry Pi with MCP and Microsoft Graph](https://cohenholmes.co.uk/blog/homelab-soc-raspberry-pi).

> **Status:** the write-up describes a working v1, and the source for both MCP servers is now published here.

## Want to build your own?

Rather than a static setup guide, this repo has [`SETUP-WITH-AI.md`](./SETUP-WITH-AI.md) — a file written as instructions *to an AI assistant*, not to you. Paste the whole thing into Claude, ChatGPT, or whatever you use, and it'll interview you (your hardware, your Entra access, which signals you actually want) and walk you through the setup adapted to your actual answers, rather than assuming you're on a Raspberry Pi 4 like the original.

## Architecture

Three layers, all running via Docker Compose on the Pi:

```
MacBook Pro (Claude Desktop)
  └── npx mcp-remote → Pi:8080  (mcp-tailscale)
  └── npx mcp-remote → Pi:8090  (mcp-graph)

Raspberry Pi 4 (on the tailnet)
  └── mcp-tailscale  :8080  — Tailscale API (+ opt-in shell/Docker admin)
  └── mcp-graph      :8090  — Microsoft Graph tools (+ opt-in account mutations)
  └── dashboard      :8081  — Web UI
  └── ntfy           :8082  — Push notifications
  └── alerter              — Polling + alerts
```

- **The Pi** runs everything. `mcp-tailscale` talks to the Tailscale API; `mcp-graph` connects to Microsoft Graph using a registered Entra app with the client credentials flow. A Starlette-based dashboard renders the web UI with no JS framework and no build step. A separate alerter container polls both sources and fires push notifications via a self-hosted [ntfy](https://ntfy.sh) instance.
- **Administration is opt-in, in both servers.** By default neither server exposes anything beyond read-only monitoring: `mcp-tailscale` has no shell or Docker socket access (`run_command`/`restart_service`/`get_logs`/`disk_usage` are absent from its tool list entirely, not just refusing calls), and `mcp-graph` can't disable or delete accounts (`disable_user`/`delete_user` likewise absent). Opting into either needs an explicit `ENABLE_ADMIN_TOOLS=true` / `ENABLE_ACCOUNT_MUTATIONS=true` in that server's `.env` — and for `mcp-tailscale`'s Docker socket specifically, also bringing the stack up with `docker-compose.admin.yml` (`docker compose -f docker-compose.yml -f docker-compose.admin.yml up -d`). Both servers also mount one narrow `SHARED_DATA_DIR` (just the shared action log) instead of a full home directory.
- **Tailscale** is the backbone. Both compose files publish every port bound to `BIND_ADDR`, which defaults to `127.0.0.1` — nothing is reachable from anywhere until you set it. Set `BIND_ADDR` in each service's `.env` to your Pi's Tailscale IP (`tailscale ip -4`) to make these ports reachable over the tailnet, and only the tailnet — they're never bound to `0.0.0.0`, so the Pi's LAN interface and the internet can't reach them regardless of firewall state.
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

[MIT](./LICENSE)
