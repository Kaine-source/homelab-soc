import os
import time
import httpx
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

TAILSCALE_API_KEY = os.getenv("TAILSCALE_API_KEY")
TAILSCALE_TAILNET = os.getenv("TAILSCALE_TAILNET")
NTFY_URL          = os.getenv("NTFY_URL", "http://ntfy:80")
NTFY_TOPIC        = os.getenv("NTFY_TOPIC", "homelab-alerts")
POLL_INTERVAL     = int(os.getenv("POLL_INTERVAL", "60"))
BASE_URL          = "https://api.tailscale.com/api/v2"

GRAPH_TENANT_ID     = os.getenv("GRAPH_TENANT_ID")
GRAPH_CLIENT_ID     = os.getenv("GRAPH_CLIENT_ID")
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET")

GRAPH_AVAILABLE = all([GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET])

# ── Auth token cache ──────────────────────────────────────────────────────────

_token_cache: dict = {}

def get_graph_token() -> str | None:
    if not GRAPH_AVAILABLE:
        return None
    now = datetime.now(timezone.utc)
    if _token_cache.get("token") and _token_cache.get("expiry", now) > now:
        return _token_cache["token"]
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(
                f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token",
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     GRAPH_CLIENT_ID,
                    "client_secret": GRAPH_CLIENT_SECRET,
                    "scope":         "https://graph.microsoft.com/.default",
                },
            )
            r.raise_for_status()
            tok = r.json()
            _token_cache["token"]  = tok["access_token"]
            _token_cache["expiry"] = now + timedelta(seconds=tok.get("expires_in", 3600) - 300)
            return _token_cache["token"]
    except Exception as e:
        print(f"[graph-auth] token error: {e}")
        return None


def graph_get(path: str) -> dict:
    token = get_graph_token()
    if not token:
        return {}
    url = f"https://graph.microsoft.com/v1.0{path}"
    with httpx.Client(timeout=15) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()


# ── Tailscale helpers ─────────────────────────────────────────────────────────

def ts_headers():
    return {"Authorization": f"Bearer {TAILSCALE_API_KEY}"}


def get_devices():
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{BASE_URL}/tailnet/{TAILSCALE_TAILNET}/devices",
            headers=ts_headers(),
        )
        r.raise_for_status()
        return r.json().get("devices", [])


def is_online(device: dict) -> bool:
    return device.get("connectedToControl", False)


# ── ntfy ─────────────────────────────────────────────────────────────────────

def notify(title: str, body: str, priority: str = "default", tags: list[str] | None = None):
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    try:
        with httpx.Client(timeout=5) as client:
            r = client.post(f"{NTFY_URL}/{NTFY_TOPIC}", content=body.encode(), headers=headers)
            r.raise_for_status()
            print(f"  [ntfy] sent: {title}")
    except Exception as exc:
        print(f"  [ntfy] failed: {exc}")


# ── Graph checks ──────────────────────────────────────────────────────────────

def check_signin_failures(state: dict):
    """Alert if >10 sign-in failures in the last hour."""
    if not GRAPH_AVAILABLE:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        data  = graph_get(f"/auditLogs/signIns?$filter=createdDateTime ge {cutoff}&$top=200&$select=status,userPrincipalName")
        signs = data.get("value", [])
        fails = [s for s in signs if s.get("status", {}).get("errorCode", 0) != 0]
        count = len(fails)
        prev  = state.get("signin_fail_count", 0)

        if count > 10 and count > prev:
            users = list({s.get("userPrincipalName", "unknown") for s in fails})[:5]
            notify(
                f"⚠️ Sign-in Spike: {count} failures",
                f"{count} failed sign-ins in the last hour\nAccounts: {', '.join(users)}",
                priority="high",
                tags=["warning", "lock"],
            )
        state["signin_fail_count"] = count
        print(f"[graph] sign-in check: {len(signs)} attempts, {count} failures")
    except Exception as e:
        print(f"[graph] sign-in check error: {e}")


def check_mfa_gaps(state: dict):
    """Alert when a new enabled user has no MFA registered."""
    if not GRAPH_AVAILABLE:
        return
    try:
        auth_data = graph_get("/reports/authenticationMethods/userRegistrationDetails?$select=userPrincipalName,isMfaRegistered")
        auth_users = auth_data.get("value", [])
        usr_data   = graph_get("/users?$select=userPrincipalName,accountEnabled&$top=200")
        disabled   = {u["userPrincipalName"] for u in usr_data.get("value", []) if not u.get("accountEnabled", True)}
        gaps       = {u["userPrincipalName"] for u in auth_users
                      if not u.get("isMfaRegistered", False)
                      and u.get("userPrincipalName", "") not in disabled}

        prev_gaps = set(state.get("mfa_gaps", []))
        new_gaps  = gaps - prev_gaps

        if new_gaps:
            for upn in new_gaps:
                notify(
                    "🔓 New MFA Gap Detected",
                    f"{upn} is enabled but has no MFA registered",
                    priority="high",
                    tags=["warning", "key"],
                )
        state["mfa_gaps"] = list(gaps)
        print(f"[graph] MFA check: {len(gaps)} gap(s), {len(new_gaps)} new")
    except Exception as e:
        print(f"[graph] MFA check error: {e}")


def check_ca_policies(state: dict):
    """Alert if an enabled CA policy changes state."""
    if not GRAPH_AVAILABLE:
        return
    try:
        data    = graph_get("/identity/conditionalAccess/policies?$select=id,displayName,state")
        policies = data.get("value", [])
        current  = {p["id"]: {"name": p.get("displayName", p["id"]), "state": p.get("state")} for p in policies}
        prev     = state.get("ca_policies", {})

        for pid, info in current.items():
            if pid in prev and prev[pid]["state"] == "enabled" and info["state"] != "enabled":
                notify(
                    "🚨 CA Policy Disabled",
                    f"'{info['name']}' changed from enabled → {info['state']}",
                    priority="urgent",
                    tags=["rotating_light", "shield"],
                )
                print(f"[graph] CA policy disabled: {info['name']}")

        state["ca_policies"] = current
        print(f"[graph] CA check: {len(policies)} policies")
    except Exception as e:
        print(f"[graph] CA check error: {e}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("Alerter starting — bootstrapping device state...")
    if GRAPH_AVAILABLE:
        print("Graph API credentials found — security checks enabled")
    else:
        print("Graph API credentials not set — only Tailscale checks active")

    devices   = get_devices()
    known_ids = {d["id"] for d in devices}
    state: dict = {
        d["id"]: {"online": is_online(d), "name": d.get("hostname", d["id"])}
        for d in devices
    }
    print(f"Tracking {len(state)} devices: {[v['name'] for v in state.values()]}")
    print(f"Polling every {POLL_INTERVAL}s — topic: {NTFY_URL}/{NTFY_TOPIC}\n")

    graph_state: dict = {}
    tick = 0

    while True:
        time.sleep(POLL_INTERVAL)
        tick += 1

        # ── Tailscale: device online/offline ──────────────────────────────────
        try:
            current     = get_devices()
            current_ids = {d["id"] for d in current}

            for d in current:
                if d["id"] not in known_ids:
                    name = d.get("hostname", d["id"])
                    ip   = d.get("addresses", ["?"])[0]
                    print(f"New device: {name} ({ip})")
                    notify(
                        "⚠️ New Device on Tailnet",
                        f"{name} just joined\nIP: {ip}",
                        priority="high",
                        tags=["warning", "computer"],
                    )
                    known_ids.add(d["id"])
                    state[d["id"]] = {"online": is_online(d), "name": name}

            for d in current:
                did        = d["id"]
                if did not in state:
                    continue
                name       = d.get("hostname", did)
                was_online = state[did]["online"]
                now_online = is_online(d)

                if was_online and not now_online:
                    print(f"OFFLINE: {name}")
                    notify("🔴 Device Offline", f"{name} has gone offline", tags=["red_circle", "computer"])
                elif not was_online and now_online:
                    print(f"ONLINE:  {name}")
                    notify("🟢 Device Back Online", f"{name} is back online", tags=["green_circle", "computer"])

                state[did] = {"online": now_online, "name": name}

        except Exception as exc:
            print(f"Poll error (tailscale): {exc}")

        # ── Graph: every 5 min (sign-ins), 10 min (CA), 15 min (MFA) ─────────
        if tick % 5 == 0:
            check_signin_failures(graph_state)
        if tick % 10 == 0:
            check_ca_policies(graph_state)
        if tick % 15 == 0:
            check_mfa_gaps(graph_state)


if __name__ == "__main__":
    main()
