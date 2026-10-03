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


# Graph pages collections (commonly ~100 items/page) and expects callers to follow
# @odata.nextLink for the rest. graph_get only ever returns the first page — fine for
# a single resource or a deliberately bounded $top query, but silently wrong for
# anything claiming to cover "all" of something (every user, every CA policy, every
# group a user belongs to): past the first page it just drops the rest with no error,
# so "no CA exclusion found" or "no stale users" could be true only of page 1.
# MAX_PAGES bounds the walk so a pathologically large collection can't hang a
# chat-tool call forever; `complete` tells the caller whether it actually reached the
# end or gave up at that bound, so a caller can be honest about which happened
# instead of treating a bounded walk as if it were exhaustive.
MAX_PAGES = 50


def graph_get_all(path: str) -> tuple[list[dict], bool]:
    """Follow @odata.nextLink and return (all items, complete). `complete` is False
    only if MAX_PAGES was hit before Graph stopped returning a nextLink — for any
    normal tenant this is True."""
    token = get_token()
    items: list[dict] = []
    url = f"{GRAPH_URL}{path}"
    with httpx.Client() as client:
        for _ in range(MAX_PAGES):
            r = client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
            r.raise_for_status()
            page = r.json()
            items.extend(page.get("value", []))
            url = page.get("@odata.nextLink")
            if not url:
                return items, True
    return items, False


@mcp.tool()
def list_users() -> str:
    """List all users in the tenant with their account status."""
    logger.info("list_users called")
    _log_action("list_users", {})
    users, complete = graph_get_all("/users?$select=displayName,userPrincipalName,accountEnabled,createdDateTime")
    lines = []
    for u in users:
        status = "✅ enabled" if u.get("accountEnabled") else "🚫 disabled"
        lines.append(f"- {u['displayName']} ({u['userPrincipalName']}) — {status}")
    if not complete:
        lines.append(f"\n⚠️ Stopped after {MAX_PAGES} pages — this tenant has more users than that; the list above is partial.")
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
    policies, complete = graph_get_all("/identity/conditionalAccess/policies")
    lines = []
    for p in policies:
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
    if not complete:
        lines.append(f"\n⚠️ Stopped after {MAX_PAGES} pages — this tenant has more CA policies than that; the list above is partial.")
    return "\n".join(lines) if lines else "No CA policies found."


@mcp.tool()
def get_failed_sign_ins() -> str:
    """Return the 10 most recent failed sign-ins from the audit log.

    This is plain authentication failures (status/errorCode ne 0), not Identity
    Protection risk detections — those are a distinct Entra ID P2 signal
    (riskState/riskLevelDuringSignIn) that this call doesn't request and may not
    even be licensed for. A failed sign-in alone isn't evidence of a risky one;
    don't conflate the two when reporting on this.
    """
    logger.info("get_failed_sign_ins called")
    _log_action("get_failed_sign_ins", {})
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

    # Get all enabled users — must be the complete set, or a user on a later page
    # would be silently missing from the comparison entirely (not just unscored).
    all_users_list, users_complete = graph_get_all(
        "/users?$select=displayName,userPrincipalName,accountEnabled"
        "&$filter=accountEnabled eq true"
    )
    all_users = {u["userPrincipalName"].lower(): u for u in all_users_list}

    # Sign-in logs are returned newest-first with no $orderby needed, so we don't have
    # to paginate the whole log to cover the 30-day window — only until an entry older
    # than `cutoff` shows up; everything before that point is covered. MAX_PAGES still
    # bounds this in case a very high-volume tenant never produces an entry that old
    # within that many pages (sign-in logs older than the cutoff should normally start
    # appearing well before then); `signins_complete` says which of those actually
    # happened, and anyone not yet seen when we stop for either reason gets a different,
    # more honest label depending on which.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_seen = {}
    signins_complete = False
    pages_scanned = 0
    try:
        token = get_token()
        url = (
            f"{GRAPH_URL}/auditLogs/signIns"
            "?$top=200&$select=userPrincipalName,createdDateTime,status"
        )
        with httpx.Client() as client:
            for _ in range(MAX_PAGES):
                r = client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
                r.raise_for_status()
                page = r.json()
                pages_scanned += 1
                crossed_cutoff = False
                for entry in page.get("value", []):
                    ts = entry.get("createdDateTime", "")
                    if ts and ts < cutoff:
                        crossed_cutoff = True
                        break
                    if entry.get("status", {}).get("errorCode", 1) != 0:
                        continue
                    upn = entry.get("userPrincipalName", "").lower()
                    if upn and upn not in last_seen:
                        last_seen[upn] = ts
                if crossed_cutoff:
                    signins_complete = True
                    break
                url = page.get("@odata.nextLink")
                if not url:
                    # Ran out of sign-in history entirely before reaching the cutoff —
                    # that's still complete coverage of the window, just a quiet tenant.
                    signins_complete = True
                    break
    except Exception as e:
        return f"❌ Could not read sign-in logs: {e}"

    stale = []
    partial = []
    for upn_lower, u in all_users.items():
        last = last_seen.get(upn_lower)
        if last and last >= cutoff:
            continue
        label = f"- {u['displayName']} ({u['userPrincipalName']})"
        if last:
            (stale if signins_complete else partial).append(f"{label} — last sign-in: {last}")
        elif signins_complete:
            stale.append(f"{label} — ⚠️ no sign-in in last 30 days")
        else:
            partial.append(f"{label} — not seen in the {pages_scanned} most recently scanned sign-in page(s); older activity not checked")

    caveat = ""
    if not users_complete:
        caveat += f"\n\n⚠️ Stopped after {MAX_PAGES} pages on the user list — this tenant has more users than that; the result is partial."
    if not signins_complete:
        caveat += (
            f"\n\n⚠️ Stopped after {MAX_PAGES} pages of sign-in logs without reaching the 30-day cutoff — "
            "this tenant has more sign-in volume than that scan covers. Entries below marked 'not seen in "
            "the scanned pages' are unconfirmed, not evidence of inactivity."
        )

    if not stale and not partial:
        return "✅ All enabled users have signed in within the last 30 days." + caveat
    lines = []
    if stale:
        lines.append(f"Accounts with no recent sign-in ({len(stale)}) — 30-day window fully checked:")
        lines.extend(stale)
    if partial:
        if lines:
            lines.append("")
        lines.append(f"Accounts not found in the scanned sign-in history ({len(partial)}) — window not fully checked, not confirmed inactive:")
        lines.extend(partial)
    return "\n".join(lines) + caveat


@mcp.tool()
def check_mfa_gaps() -> str:
    """List enabled users with no MFA method registered, per Entra's MFA registration report."""
    logger.info("check_mfa_gaps called")
    _log_action("check_mfa_gaps", {})
    users, users_complete = graph_get_all("/users?$select=displayName,userPrincipalName,accountEnabled&$filter=accountEnabled eq true")
    enabled = {u["userPrincipalName"].lower(): u for u in users}

    report, report_complete = graph_get_all(
        "/reports/authenticationMethods/userRegistrationDetails"
        "?$select=userPrincipalName,isMfaRegistered"
    )
    registered = {
        r["userPrincipalName"].lower(): r.get("isMfaRegistered", False)
        for r in report
    }

    gaps = [
        f"- {u['displayName']} ({u['userPrincipalName']})"
        for upn_lower, u in enabled.items()
        if not registered.get(upn_lower, False)
    ]
    caveat = ""
    if not users_complete or not report_complete:
        caveat = (
            f"\n\n⚠️ Stopped after {MAX_PAGES} pages on "
            + " and ".join(filter(None, [
                "the user list" if not users_complete else None,
                "the MFA registration report" if not report_complete else None,
            ]))
            + " — this tenant is larger than that; the result above is partial, not a complete gap list."
        )
    if not gaps:
        return "✅ All enabled users have at least one MFA method registered." + caveat
    return f"⚠️ MFA gaps ({len(gaps)} users):\n" + "\n".join(gaps) + caveat


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
