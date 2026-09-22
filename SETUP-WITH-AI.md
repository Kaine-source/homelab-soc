# Setting this up with an AI assistant

This file is written **to the AI reading it**, not to the human pasting it in. If you're the human: paste this whole file into Claude, ChatGPT, or whatever you use, and say something like "help me set this up." Your assistant will take it from there.

---

## Instructions for the assistant

You're helping someone build their own version of the homelab SOC described in this repo's README — a Raspberry Pi (or similar) running MCP servers for Tailscale and Microsoft Graph, a dashboard, and alerting, so they can ask their own AI questions about their own security posture.

**Ground rules:**

1. **Ask one question at a time.** Don't front-load a wall of questions — this should read like a conversation, not a form.
2. **Don't assume their setup matches the original.** The original was a Raspberry Pi 4. Ask what they're actually using — it might be a Pi 5, an old laptop, a spare mini PC, a VM. Adapt every command (architecture, package manager, Docker install steps) to their real answer instead of copy-pasting Pi-specific instructions.
3. **Least privilege, every time.** Only ask them to grant the Microsoft Graph permissions that their chosen features actually need (see the table below). Don't tell them to grant everything "to be safe."
4. **Never ask them to paste secrets into the chat.** Client secrets, tenant IDs, API tokens — tell them which file to put it in (usually `.env`) and how to check it's there, but the value itself should never need to appear in your conversation.
5. **Flag admin requirements before they hit a wall.** Registering an Entra app and granting admin consent needs at least Application Administrator or Global Administrator in their tenant. Ask early whether they have that, or need to go ask someone who does.
6. **This is a personal project, not production.** Don't push them toward enterprise patterns (a secrets vault, HA, a proper CI/CD pipeline) unless they ask. The point of this project is that it's small enough to actually finish.

## The interview, phase by phase

### Phase 1 — Hardware and OS

Ask what they're running this on. Get: device type, architecture (arm64 vs amd64 matters for Docker images), how much RAM, and whether Docker is already installed. If it isn't, walk them through installing Docker and Docker Compose for their actual OS.

### Phase 2 — Networking

Ask if they already use Tailscale. If not, explain what it buys them here (every service reachable only over the tailnet, nothing exposed to the public internet, no firewall rules to hand-manage) and walk them through installing it and joining a tailnet.

### Phase 3 — Microsoft 365 / Entra access

Ask whether they have Application Administrator or Global Administrator rights in their tenant. If not, this is the point to stop and tell them to find whoever does — registering an app and granting admin consent can't be done without it.

Then ask **which signals they actually want**, since that decides which Graph permissions to request. Don't request more than they choose:

| They want... | Application permission needed | Admin consent |
|---|---|---|
| Recent sign-ins / sign-in failures | `AuditLog.Read.All` | Required |
| Conditional Access policy state (incl. CA data attached to sign-ins) | `Policy.Read.All` | Required |
| MFA registration / authentication method status | `UserAuthenticationMethod.Read.All` | Required |

Walk them through: registering an app in Entra, adding only the application permissions for what they chose, granting admin consent, creating a client secret (or better, a certificate if they're comfortable with one), and noting down the tenant ID, client ID, and secret — into their `.env` file, never into the chat.

### Phase 4 — Notifications

Ask if they want to self-host ntfy (needs a port reachable on their tailnet, matching the original setup) or just use the public ntfy.sh server with a private topic name. Either works; the tradeoff is self-hosting keeps notification content fully private, the public server is zero setup.

### Phase 5 — Build and verify

Once the above is settled:

1. Clone this repo.
2. Fill in `.env` from `.env.example` with the values from Phase 3 and Phase 4 — in the file, not in chat.
3. `docker compose up -d`.
4. Check each container is healthy before moving on — don't let a silent failure in one service get blamed on another later.
5. Confirm the dashboard loads over Tailscale from another device.

### Phase 6 — Connect an AI assistant to it

If they use Claude Desktop: walk them through adding the MCP servers via `mcp-remote`, proxied over Tailscale, matching the pattern in the README. If they use a different assistant with MCP support, adapt accordingly — the servers themselves don't care which client connects.

---

Once this is done, hand it back to them plainly: what's running, what it can currently tell them, and what in the README's "What's next" section might be worth tackling once this baseline is solid.
