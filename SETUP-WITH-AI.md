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
5. **Flag admin requirements before they hit a wall.** Registering the app registration itself needs Application Administrator or Cloud Application Administrator. But granting admin consent for *Microsoft Graph application permissions* specifically — which is what every feature here needs — is narrower: Application Administrator is explicitly excluded from that one case. It takes Global Administrator or Privileged Role Administrator. Ask early whether they have one of those two, or need to go ask someone who does; don't let them discover the gap after they've already registered the app.
6. **This is a personal project, not production.** Don't push them toward enterprise patterns (a secrets vault, HA, a proper CI/CD pipeline) unless they ask. The point of this project is that it's small enough to actually finish.

## The interview, phase by phase

### Phase 1 — Hardware and OS

Ask what they're running this on. Get: device type, architecture (arm64 vs amd64 matters for Docker images), how much RAM, and whether Docker is already installed. If it isn't, walk them through installing Docker and Docker Compose for their actual OS.

### Phase 2 — Networking

Ask if they already use Tailscale. If not, explain what it buys them here (every service reachable only over the tailnet, nothing exposed to the public internet, no firewall rules to hand-manage) and walk them through installing it and joining a tailnet.

Also ask now, not later: **which AI client will they use, and on what device?** MCP support in a client doesn't by itself mean it can reach the Pi — the client's device also needs to be on the same tailnet (install Tailscale there too, if it isn't already). If they plan to run the client on the Pi itself, that's simpler but still worth confirming explicitly rather than assuming.

### Phase 3 — Microsoft 365 / Entra access

Ask whether they have Global Administrator or Privileged Role Administrator rights in their tenant (see ground rule 5 — Application Administrator can register the app, but can't grant admin consent for Graph application permissions). If not, this is the point to stop and tell them to find whoever does.

Then ask **which signals they actually want**, since that decides which Graph permissions to request. Don't request more than they choose:

| They want... | Application permission needed | Admin consent |
|---|---|---|
| Recent sign-ins / sign-in failures (`get_risky_sign_ins`) | `AuditLog.Read.All` | Required |
| Conditional Access policy state (incl. CA data attached to sign-ins) | `Policy.Read.All` | Required |
| MFA registration status report (`check_mfa_gaps`) | `AuditLog.Read.All` | Required |
| Per-user authentication methods (`get_user`) | `UserAuthenticationMethod.Read.All` | Required |
| User directory listing, stale-account detection, or per-user detail (`list_users`, `get_user`, `list_stale_users`, `check_mfa_gaps`) | `User.Read.All` | Required |
| Disabling a user account (`disable_user`) | `User.EnableDisableAccount.All` + `User.Read.All` | Required |
| Deleting a user account (`delete_user`) | `User.ReadWrite.All`, `Directory.Read.All`, `Policy.Read.All`, `UserAuthenticationMethod.Read.All` | Required |

Two things worth knowing about that table:

- **`check_mfa_gaps` needs `AuditLog.Read.All`, not `UserAuthenticationMethod.Read.All`**, even though it's about MFA — it reads the `userRegistrationDetails` *report*, a different endpoint from the per-user authentication-methods list `get_user` reads, and the two take different permissions. Easy to conflate; they aren't interchangeable.
- **`delete_user` needs all four permissions listed, not just `User.ReadWrite.All`** — but not because `delete_user` enforces this itself. As the code stands today, `delete_user(confirm=True)` goes straight to the Graph `DELETE` call; it does **not** call `pre_delete_check` automatically, and `pre_delete_check` reports permission failures as warnings, not hard blockers. The four permissions are needed because `pre_delete_check` is a **separate tool the assistant must call and review itself**, every time, before ever calling `delete_user(confirm=True)` — it checks group memberships and app role assignments (`Directory.Read.All`), Conditional Access exclusions (`Policy.Read.All`) and registered auth methods (`UserAuthenticationMethod.Read.All`) as well as the account itself. Treat "run `pre_delete_check`, read every line, confirm zero blockers" as a mandatory manual step in your own process, not something the tooling guarantees for you. Missing any of those permissions makes that manual check unreliable, not just a one-off failure.

Two things the checklist can't check at all, regardless of permissions — Microsoft Graph doesn't support application-permission access to a user's owned objects or licence assignments. `pre_delete_check` flags both as a fixed manual-review note rather than pretending to check them; tell them to look at the user's "Owned objects" and licence assignments in the Entra admin center themselves before deleting.

**If they chose `disable_user` or `delete_user`, the extra directory role is only needed for privileged targets.** For an ordinary, non-administrator account, the Graph application permissions above are sufficient on their own — nothing extra needs to be assigned to the app's service principal. Microsoft's own reference is explicit that the stricter requirement (the app's service principal also needing a directory role such as **User Administrator**) only applies when the *target* account itself holds a privileged Entra admin role — not as a blanket rule for every app-only `disable_user`/`delete_user` call. Don't have them grant a broad directory role "just in case." The simpler and safer instruction: these two tools should never be pointed at an account that holds an Entra admin role at all — tell the assistant using this server to treat that as a hard no, and if genuinely deleting/disabling an admin account is ever required, that's a job for a human in the Entra admin center, not this tool.

**Licensing, before promising anything:** ask what Microsoft 365/Entra licence tier they're on. Sign-in logs, the MFA registration report, and Conditional Access policies all need at least Entra ID P1 (included in Microsoft 365 Business Premium) — without it, these either come back empty or fail outright, and it's better to know that before choosing features than after.

Walk them through: registering an app in Entra, adding only the application permissions for what they chose, granting admin consent, and creating a client secret — the Graph client in this repo only implements client-secret authentication today; there's no certificate-based path in the code to fall back to, whatever earlier guidance here may have implied. Note down the tenant ID, client ID, and secret — into their `.env` file, never into the chat.

### Phase 4 — Notifications

Ask if they want to self-host ntfy (needs a port reachable on their tailnet, matching the original setup) or just use the public ntfy.sh server with a private topic name. Either works; the tradeoff is self-hosting keeps notification content fully private, the public server is zero setup. Whichever they pick, the choice goes in `NTFY_URL`/`NTFY_TOPIC` in `mcp-tailscale/.env` — those values are honoured now, not silently overridden by the compose file.

### Phase 5 — Build and verify

Once the above is settled:

1. Clone this repo.
2. Fill in `.env` from `.env.example` with the values from Phase 3 and Phase 4 — in the file, not in chat. `mcp-tailscale` and `mcp-graph` are two separate Docker Compose projects, each with their own `.env`. In both files, also set `BIND_ADDR` to their Pi's (or other host's) Tailscale IP — have them run `tailscale ip -4` and paste the result in. Left at the `.env.example` default of `127.0.0.1`, every port stays loopback-only and nothing in Phase 5 or 6 will be reachable from another tailnet device. Generate `MCP_AUTH_TOKEN` locally too (e.g. `openssl rand -hex 32`) — it's a shared secret the servers check on every request, never issued by anything, so it only needs to exist in `.env` and in the AI client's own connection config, never in this conversation.
3. `docker compose up -d`, run once inside `mcp-tailscale/` and once inside `mcp-graph/` — there's no root-level compose file, so running it from the repo root won't find either.
4. Check each container actually started cleanly — `docker compose logs` on each, looking specifically for an Entra token-acquisition failure in `mcp-graph` (bad tenant/client ID/secret) and for the "MCP_AUTH_TOKEN is not set" refusal in either server (means step 2 was skipped or the container wasn't recreated after editing `.env`). A container showing as "running" isn't the same as having started without error — read the log, don't just check `docker compose ps`.
5. From a device that's **on** the tailnet, confirm the dashboard loads at the Tailscale IP set in step 2, and that an MCP request reaches each server (a 401 means it reached the server but the bearer token didn't match — progress, not success; anything else not connecting at all means check `BIND_ADDR` again).
6. From a device that's **not** on the tailnet (or with Tailscale paused on the test device), confirm the same ports are unreachable. This is the other half of step 5 — "reachable from the tailnet" only means something if "unreachable from everywhere else" is also true, and it's cheap to check now rather than assume it.
7. Trigger one real end-to-end check per thing they chose: a live tool call from the connected AI client (not just a container health check) for each MCP server, and — if notifications were set up — force one through (the alerter polls on `POLL_INTERVAL`; or trigger a condition it watches for) and confirm it actually lands in ntfy.

Treat a failure at any of these as a stop, not a note-to-self — fix it before moving to the next step, the same way a broken foundation doesn't get built over.

### Phase 6 — Connect an AI assistant to it

If they use Claude Desktop: walk them through adding the MCP servers via `mcp-remote`, proxied over Tailscale, matching the pattern in the README. Both servers check every request's `Authorization` header against `MCP_AUTH_TOKEN` (a plain bearer-token comparison — see Phase 5 step 2 for generating it); whatever client they use needs to be configured to send that same token as a `Bearer` header on its requests to the server, which is usually a connection-level setting (a custom-header option, an auth config block) rather than something typed into a chat turn — check that specific client's own docs for how it takes custom headers, since this varies by client and isn't something to guess at. If they use a different assistant with MCP support, adapt accordingly — the servers themselves don't care which client connects, only that it presents the right token.

---

Once this is done, hand it back to them plainly: what's running, what it can currently tell them, and what in the README's "What's next" section might be worth tackling once this baseline is solid.
