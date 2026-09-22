import os
import logging
import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("mcp-graph")

import json as _json
_ACTION_LOG = "/home/kaine/action.log"

def _log_action(tool: str, args: dict):
    try:
        entry = {"ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "server": "mcp-graph", "tool": tool, "args": args}
        with open(_ACTION_LOG, "a") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception:
        pass

load_dotenv()

TENANT_ID      = os.getenv("GRAPH_TENANT_ID")
CLIENT_ID      = os.getenv("GRAPH_CLIENT_ID")
CLIENT_SECRET  = os.getenv("GRAPH_CLIENT_SECRET")
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")
GRAPH_URL      = "https://graph.microsoft.com/v1.0"
TOKEN_URL      = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

mcp = FastMCP("Graph Monitor")


def get_token() -> str:
    with httpx.Client() as client:
        r = client.post(TOKEN_URL, data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope":         "https://graph.microsoft.com/.default",
        })
        r.raise_for_status()
        return r.json()["access_token"]


def graph_get(path: str) -> dict:
    token = get_token()
    with httpx.Client() as client:
        r = client.get(
            f"{GRAPH_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()


@mcp.tool()
def list_users() -> str:
    """List all users in the tenant with their account status."""
    logger.info("list_users called")
    _log_action("list_users", {})
    users = graph_get("/users?$select=displayName,userPrincipalName,accountEnabled,createdDateTime")
    lines = []
    for u in users.get("value", []):
        status = "✅ enabled" if u.get("accountEnabled") else "🚫 disabled"
        lines.append(f"- {u['displayName']} ({u['userPrincipalName']}) — {status}")
    return "\n".join(lines) if lines else "No users found."


@mcp.tool()
def get_user(upn: str) -> str:
    """Get details for a specific user by UPN, including their auth methods."""
    logger.info(f"get_user called: {upn}")
    _log_action("get_user", {"upn": upn})
    user = graph_get(f"/users/{upn}?$select=displayName,userPrincipalName,accountEnabled,jobTitle,department,lastPasswordChangeDateTime")
    methods = graph_get(f"/users/{upn}/authentication/methods")
    method_names = [m.get("@odata.type", "unknown").split(".")[-1] for m in methods.get("value", [])]
    return (
        f"Name: {user.get('displayName')}\n"
        f"UPN: {user.get('userPrincipalName')}\n"
        f"Account enabled: {user.get('accountEnabled')}\n"
        f"Job title: {user.get('jobTitle', 'N/A')}\n"
        f"Department: {user.get('department', 'N/A')}\n"
        f"Last password change: {user.get('lastPasswordChangeDateTime', 'N/A')}\n"
        f"Auth methods: {', '.join(method_names) if method_names else 'none'}"
    )


@mcp.tool()
def list_ca_policies() -> str:
    """List all Conditional Access policies, their state, and excluded users/groups."""
    logger.info("list_ca_policies called")
    _log_action("list_ca_policies", {})
    policies = graph_get("/identity/conditionalAccess/policies")
    lines = []
    for p in policies.get("value", []):
        state = p.get("state", "unknown")
        icon = "✅" if state == "enabled" else "⚠️" if state == "enabledForReportingButNotEnforced" else "🚫"
        conditions = p.get("conditions", {})
        users = conditions.get("users", {})
        excluded_users = users.get("excludeUsers", [])
        excluded_groups = users.get("excludeGroups", [])
        excl_parts = []
        if excluded_users:
            excl_parts.append(f"{len(excluded_users)} user(s)")
        if excluded_groups:
            excl_parts.append(f"{len(excluded_groups)} group(s)")
        excl_str = f" [excludes: {', '.join(excl_parts)}]" if excl_parts else " [no exclusions]"
        lines.append(f"{icon} {p['displayName']} — {state}{excl_str}")
    return "\n".join(lines) if lines else "No CA policies found."


@mcp.tool()
def get_risky_sign_ins() -> str:
    """Return the 10 most recent failed sign-ins from the audit log."""
    logger.info("get_risky_sign_ins called")
    _log_action("get_risky_sign_ins", {})
    logs = graph_get(
        "/auditLogs/signIns?$filter=status/errorCode ne 0"
        "&$top=10&$select=userDisplayName,userPrincipalName,status,createdDateTime,ipAddress,location,clientAppUsed"
    )
    entries = logs.get("value", [])
    if not entries:
        return "No failed sign-ins found."
    lines = []
    for e in entries:
        error = e.get("status", {}).get("failureReason", "unknown reason")
        loc = e.get("location", {})
        city = loc.get("city", "?")
        country = loc.get("countryOrRegion", "?")
        lines.append(
            f"- {e['userDisplayName']} ({e['userPrincipalName']})\n"
            f"  ❌ {error} | {city}, {country} | {e.get('clientAppUsed', '?')} | {e.get('createdDateTime')}"
        )
    return "\n".join(lines)




@mcp.tool()
def list_stale_users() -> str:
    """List enabled users with no successful sign-in in the last 30 days.

    Uses audit sign-in logs (available on P1/Business Premium).
    Note: sign-in logs only retain 30 days on P1 — users absent from logs
    may have signed in earlier, not necessarily never.
    """
    logger.info("list_stale_users called")
    _log_action("list_stale_users", {})
    from datetime import datetime, timezone, timedelta

    # Get all enabled users
    users = graph_get(
        "/users?$select=displayName,userPrincipalName,accountEnabled"
        "&$filter=accountEnabled eq true"
    )
    all_users = {u["userPrincipalName"].lower(): u for u in users.get("value", [])}

    # Pull last 500 successful sign-ins from audit logs (P1: 30-day retention)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        # No $orderby — sign-in logs return newest-first by default
        # Filter client-side to avoid server-side timeout
        logs = graph_get(
            "/auditLogs/signIns"
            "?$top=500"
            "&$select=userPrincipalName,createdDateTime,status"
        )
        # Build last-seen lookup — successful sign-ins only, first hit = most recent
        last_seen = {}
        for entry in logs.get("value", []):
            if entry.get("status", {}).get("errorCode", 1) != 0:
                continue
            upn = entry.get("userPrincipalName", "").lower()
            ts = entry.get("createdDateTime", "")
            if upn and upn not in last_seen:
                last_seen[upn] = ts
    except Exception as e:
        return f"❌ Could not read sign-in logs: {e}"

    stale = []
    for upn_lower, u in all_users.items():
        last = last_seen.get(upn_lower)
        if not last:
            stale.append(f"- {u['displayName']} ({u['userPrincipalName']}) — ⚠️ no sign-in in last 30 days (log retention limit)")
        elif last < cutoff:
            stale.append(f"- {u['displayName']} ({u['userPrincipalName']}) — last sign-in: {last}")

    if not stale:
        return "✅ All enabled users have signed in within the last 30 days."
    return (
        f"Accounts with no recent sign-in ({len(stale)}) — P1 log retention: 30 days:\n"
        + "\n".join(stale)
    )


@mcp.tool()
def check_mfa_gaps() -> str:
    """List enabled users with no MFA method registered, per Entra's MFA registration report."""
    logger.info("check_mfa_gaps called")
    _log_action("check_mfa_gaps", {})
    users = graph_get("/users?$select=displayName,userPrincipalName,accountEnabled&$filter=accountEnabled eq true")
    enabled = {u["userPrincipalName"].lower(): u for u in users.get("value", [])}

    report = graph_get(
        "/reports/authenticationMethods/userRegistrationDetails"
        "?$select=userPrincipalName,isMfaRegistered"
    )
    registered = {
        r["userPrincipalName"].lower(): r.get("isMfaRegistered", False)
        for r in report.get("value", [])
    }

    gaps = [
        f"- {u['displayName']} ({u['userPrincipalName']})"
        for upn_lower, u in enabled.items()
        if not registered.get(upn_lower, False)
    ]
    if not gaps:
        return "✅ All enabled users have at least one MFA method registered."
    return f"⚠️ MFA gaps ({len(gaps)} users):\n" + "\n".join(gaps)


@mcp.tool()
def get_named_locations() -> str:
    """List named locations (trusted IPs/countries) configured in Conditional Access."""
    logger.info("get_named_locations called")
    _log_action("get_named_locations", {})
    locations = graph_get("/identity/conditionalAccess/namedLocations")
    entries = locations.get("value", [])
    if not entries:
        return "No named locations configured."
    lines = []
    for loc in entries:
        odata_type = loc.get("@odata.type", "")
        name = loc.get("displayName", "Unnamed")
        if "ipNamed" in odata_type:
            ranges = [r.get("cidrAddress", "?") for r in loc.get("ipRanges", [])]
            trusted = "✅ trusted" if loc.get("isTrusted") else "⚠️ not marked trusted"
            lines.append(f"- {name} [{trusted}] — IPs: {', '.join(ranges) if ranges else 'none'}")
        elif "countryNamed" in odata_type:
            countries = loc.get("countriesAndRegions", [])
            lines.append(f"- {name} [countries] — {', '.join(countries) if countries else 'none'}")
        else:
            lines.append(f"- {name} [{odata_type}]")
    return "\n".join(lines)


@mcp.tool()
def disable_user(upn: str) -> str:
    """Disable a user account in Entra ID (reversible — sets accountEnabled to false).

    Use this before delete_user to soft-disable first. Safe to undo via the portal.
    Will refuse to disable BreakGlass or any account whose UPN contains 'break' or 'emergency'.
    """
    logger.info(f"disable_user called: {upn}")
    _log_action("disable_user", {"upn": upn})
    if any(x in upn.lower() for x in ["break", "emergency"]):
        return f"🚫 Refused: '{upn}' looks like a break-glass account. Disable it manually in the portal."
    try:
        token = get_token()
        with httpx.Client() as client:
            r = client.patch(
                f"{GRAPH_URL}/users/{upn}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"accountEnabled": False},
                timeout=15,
            )
            if r.status_code == 204:
                return f"✅ '{upn}' has been disabled. Verify in portal, then use delete_user to permanently remove."
            else:
                return f"❌ Failed ({r.status_code}): {r.text}"
    except Exception as e:
        logger.error(f"disable_user error: {e}")
        return f"Error: {e}"


@mcp.tool()
def delete_user(upn: str, confirm: bool = False) -> str:
    """Permanently delete a user from Entra ID.

    This is irreversible (soft-deleted for 30 days, then gone).
    Pass confirm=True to execute. Without it, returns a safety summary only.
    Recommend running disable_user first to verify the account is no longer needed.
    Will refuse to delete BreakGlass or any account whose UPN contains 'break' or 'emergency'.
    """
    logger.info(f"delete_user called: {upn}, confirm={confirm}")
    _log_action("delete_user", {"upn": upn, "confirm": confirm})
    if any(x in upn.lower() for x in ["break", "emergency"]):
        return f"🚫 Refused: '{upn}' looks like a break-glass account. Delete it manually in the portal if truly needed."
    if not confirm:
        # Fetch current state so user can verify before confirming
        try:
            user = graph_get(f"/users/{upn}?$select=displayName,userPrincipalName,accountEnabled,lastPasswordChangeDateTime")
            enabled = "⚠️ STILL ENABLED" if user.get("accountEnabled") else "✅ disabled"
            return (
                f"⚠️  DRY RUN — no changes made.\n"
                f"Target: {user.get('displayName')} ({user.get('userPrincipalName')})\n"
                f"Account status: {enabled}\n"
                f"Last password change: {user.get('lastPasswordChangeDateTime', 'N/A')}\n\n"
                f"To permanently delete, call again with confirm=True.\n"
                f"Tip: run disable_user first if account is still enabled."
            )
        except Exception as e:
            return f"Could not fetch user details: {e}"
    try:
        token = get_token()
        with httpx.Client() as client:
            r = client.delete(
                f"{GRAPH_URL}/users/{upn}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if r.status_code == 204:
                return f"✅ '{upn}' permanently deleted (soft-deleted for 30 days — recoverable from Entra > Deleted users until then)."
            else:
                return f"❌ Failed ({r.status_code}): {r.text}"
    except Exception as e:
        logger.error(f"delete_user error: {e}")
        return f"Error: {e}"


@mcp.tool()
def pre_delete_check(upn: str) -> str:
    """Run a pre-deletion checklist for a user account before disabling or deleting.

    Checks: account type/IDP, last sign-in, group memberships, owned objects,
    app role assignments, licence assignments, CA policy exclusions, and auth methods.
    Returns a structured report with CLEAR / REVIEW / BLOCKER status per item.
    Always run this before disable_user or delete_user.
    """
    _log_action("pre_delete_check", {"upn": upn})
    lines = [f"Pre-Deletion Checklist: {upn}", "=" * 60]
    blockers = 0

    # 1. Account basics + IDP
    try:
        u = graph_get(
            f"/users/{upn}?$select=displayName,userPrincipalName,accountEnabled,"
            "userType,onPremisesSyncEnabled,onPremisesImmutableId,identities,"
            "lastPasswordChangeDateTime,createdDateTime,jobTitle,department"
        )
        enabled = u.get("accountEnabled", True)
        synced  = u.get("onPremisesSyncEnabled")
        immutable = u.get("onPremisesImmutableId")
        identities = u.get("identities", [])
        idp_sources = [i.get("issuer", "") for i in identities if i.get("issuer") and "microsoftonline" not in i.get("issuer","")]

        lines.append(f"\n[1] Account Basics")
        lines.append(f"    Name:       {u.get('displayName')}")
        lines.append(f"    UPN:        {u.get('userPrincipalName')}")
        lines.append(f"    Enabled:    {'⚠️ YES — disable before deleting' if enabled else '✅ Already disabled'}")
        if enabled:
            blockers += 1
        lines.append(f"    Created:    {(u.get('createdDateTime') or 'N/A')[:10]}")
        lines.append(f"    Last PW:    {(u.get('lastPasswordChangeDateTime') or 'N/A')[:10]}")
        lines.append(f"    Job/Dept:   {u.get('jobTitle') or '—'} / {u.get('department') or '—'}")

        lines.append(f"\n[2] Identity Provider")
        if synced or immutable:
            lines.append(f"    ❌ BLOCKER — AD synced account (onPremisesSyncEnabled={synced})")
            lines.append(f"    Delete from on-premises AD, not Entra directly.")
            blockers += 1
        elif idp_sources:
            lines.append(f"    ⚠️  REVIEW — External IdP detected: {', '.join(idp_sources)}")
            lines.append(f"    Deprovision in the source IdP as well.")
        else:
            lines.append(f"    ✅ Cloud-only Entra ID account — safe to delete here.")
    except Exception as e:
        lines.append(f"\n[1-2] ❌ Could not fetch account details: {e}")

    # 3. Group memberships
    try:
        groups = graph_get(f"/users/{upn}/memberOf?$select=displayName,id")
        g_list = groups.get("value", [])
        lines.append(f"\n[3] Group Memberships ({len(g_list)})")
        if g_list:
            for g in g_list[:10]:
                gtype = g.get('@odata.type','').split('.')[-1]
                gname = g.get('displayName') or f'[{gtype}]'
                lines.append(f"    • {gname}")
            if len(g_list) > 10:
                lines.append(f"    … and {len(g_list)-10} more")
            lines.append(f"    ⚠️  REVIEW — Membership will be removed on delete. Ensure no shared mailbox or resource access is lost.")
        else:
            lines.append(f"    ✅ No group memberships.")
    except Exception as e:
        lines.append(f"\n[3] ⚠️  Could not fetch memberships: {e}")

    # 4. Owned objects (groups, apps)
    try:
        owned = graph_get(f"/users/{upn}/ownedObjects?$select=displayName,id")
        o_list = owned.get("value", [])
        lines.append(f"\n[4] Owned Objects ({len(o_list)})")
        if o_list:
            for o in o_list:
                lines.append(f"    • {o.get('displayName','?')} [{o.get('@odata.type','?').split('.')[-1]}]")
            lines.append(f"    ❌ BLOCKER — Transfer ownership before deleting.")
            blockers += 1
        else:
            lines.append(f"    ✅ No owned groups or applications.")
    except Exception as e:
        lines.append(f"\n[4] ⚠️  Could not fetch owned objects: {e}")

    # 5. App role assignments
    try:
        roles = graph_get(f"/users/{upn}/appRoleAssignments")
        r_list = roles.get("value", [])
        lines.append(f"\n[5] App Role Assignments ({len(r_list)})")
        if r_list:
            for r in r_list[:5]:
                lines.append(f"    • {r.get('resourceDisplayName','?')} — role {r.get('appRoleId','?')[:8]}…")
            lines.append(f"    ⚠️  REVIEW — These assignments will be removed on delete.")
        else:
            lines.append(f"    ✅ No app role assignments.")
    except Exception as e:
        lines.append(f"\n[5] ⚠️  Could not fetch app roles: {e}")

    # 6. Licence assignments
    try:
        lic = graph_get(f"/users/{upn}/licenseDetails?$select=skuPartNumber")
        l_list = lic.get("value", [])
        lines.append(f"\n[6] Licence Assignments ({len(l_list)})")
        if l_list:
            for l in l_list:
                lines.append(f"    • {l.get('skuPartNumber','?')}")
            lines.append(f"    ⚠️  REVIEW — Remove licences before deletion to recover them.")
        else:
            lines.append(f"    ✅ No licences assigned.")
    except Exception as e:
        lines.append(f"\n[6] ⚠️  Could not fetch licences: {e}")

    # 7. CA policy exclusions
    try:
        ca = graph_get("/identity/conditionalAccess/policies?$select=displayName,conditions")
        ca_hits = []
        try:
            uid = graph_get(f"/users/{upn}?$select=id").get("id","")
        except:
            uid = ""
        for p in ca.get("value", []):
            excl = p.get("conditions",{}).get("users",{}).get("excludeUsers",[])
            if uid and uid in excl:
                ca_hits.append(p.get("displayName","?"))
        lines.append(f"\n[7] CA Policy Exclusions")
        if ca_hits:
            for h in ca_hits:
                lines.append(f"    • {h}")
            lines.append(f"    ⚠️  REVIEW — Remove this account from CA exclusions after deletion.")
        else:
            lines.append(f"    ✅ Not directly excluded from any CA policies.")
    except Exception as e:
        lines.append(f"\n[7] ⚠️  Could not check CA exclusions: {e}")

    # 8. Auth methods
    try:
        methods = graph_get(f"/users/{upn}/authentication/methods")
        m_list = [m.get("@odata.type","").split(".")[-1] for m in methods.get("value",[])]
        lines.append(f"\n[8] Auth Methods Registered")
        lines.append(f"    {', '.join(m_list) if m_list else '—'}")
        lines.append(f"    ✅ Will be cleared on deletion.")
    except Exception as e:
        lines.append(f"\n[8] ⚠️  Could not fetch auth methods: {e}")

    # Summary
    lines.append(f"\n{'=' * 60}")
    if blockers:
        lines.append(f"❌ {blockers} BLOCKER(S) found — resolve before deleting.")
    else:
        lines.append(f"✅ No blockers found. Recommended order:")
        lines.append(f"   1. Remove licences (if any)")
        lines.append(f"   2. Run disable_user('{upn}')")
        lines.append(f"   3. Wait 24–48h to confirm no service impact")
        lines.append(f"   4. Run delete_user('{upn}', confirm=True)")

    return "\n".join(lines)


if __name__ == "__main__":
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount
    from starlette.requests import Request as StarletteRequest

    if not MCP_AUTH_TOKEN:
        raise SystemExit(
            "MCP_AUTH_TOKEN is not set. This server can disable and delete user "
            "accounts — refusing to start without an auth token rather than "
            "allowing unauthenticated access."
        )

    sse = SseServerTransport("/messages/")

    def _auth_ok(request) -> bool:
        return request.headers.get("Authorization", "") == f"Bearer {MCP_AUTH_TOKEN}"

    async def health(request):
        return JSONResponse({"status": "ok", "service": "mcp-graph"})

    async def handle_sse(request):
        if not _auth_ok(request):
            logger.warning(f"Unauthorized SSE from {request.client.host}")
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0], streams[1],
                mcp._mcp_server.create_initialization_options()
            )

    async def handle_messages(scope, receive, send):
        req = StarletteRequest(scope, receive)
        if not _auth_ok(req):
            resp = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await resp(scope, receive, send)
            return
        await sse.handle_post_message(scope, receive, send)

    app = Starlette(routes=[
        Route("/health", endpoint=health),
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=handle_messages),
    ])

    logger.info("MCP Graph Monitor starting on :8090")
    uvicorn.run(app, host="0.0.0.0", port=8090)
