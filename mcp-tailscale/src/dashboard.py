import os, json, time, httpx
from html import escape
from urllib.parse import quote
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
import uvicorn

load_dotenv()

TAILSCALE_API_KEY   = os.getenv("TAILSCALE_API_KEY")
TAILSCALE_TAILNET   = os.getenv("TAILSCALE_TAILNET")
GRAPH_TENANT_ID     = os.getenv("GRAPH_TENANT_ID")
GRAPH_CLIENT_ID     = os.getenv("GRAPH_CLIENT_ID")
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET")
BASE_URL   = "https://api.tailscale.com/api/v2"
GRAPH_URL  = "https://graph.microsoft.com/v1.0"
TOKEN_URL  = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token"
ACTION_LOG = "/home/kaine/action.log"

# ── Data helpers ──────────────────────────────────────────────────────────────

def ts_headers():
    return {"Authorization": f"Bearer {TAILSCALE_API_KEY}"}

def get_devices():
    with httpx.Client() as c:
        r = c.get(f"{BASE_URL}/tailnet/{TAILSCALE_TAILNET}/devices", headers=ts_headers(), timeout=10)
        r.raise_for_status()
        return r.json().get("devices", [])

_token_cache = {"token": None, "expires": 0}

def get_graph_token():
    _now = time.time()
    if _token_cache["token"] and _now < _token_cache["expires"]:
        return _token_cache["token"]
    with httpx.Client() as c:
        r = c.post(TOKEN_URL, data={"grant_type":"client_credentials","client_id":GRAPH_CLIENT_ID,"client_secret":GRAPH_CLIENT_SECRET,"scope":"https://graph.microsoft.com/.default"})
        r.raise_for_status()
        data = r.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires"] = _now + data.get("expires_in", 3600) - 60
        return _token_cache["token"]

def graph_get(path, timeout=20):
    token = get_graph_token()
    with httpx.Client() as c:
        r = c.get(f"{GRAPH_URL}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        r.raise_for_status()
        return r.json()

def time_ago(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z","+00:00"))
        s = int((datetime.now(timezone.utc)-dt).total_seconds())
        if s < 60: return "Just now"
        if s < 3600: return f"{s//60}m ago"
        if s < 86400: return f"{s//3600}h ago"
        return f"{s//86400}d ago"
    except: return iso or "—"

def fmt_dt(iso):
    try: return iso[:19].replace("T"," ")
    except: return iso or "—"

# ── Shared layout ─────────────────────────────────────────────────────────────

NAV_ITEMS = [
    ("overview",  "/",         "Overview",      "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"),
    ("tailnet",   "/tailnet",  "Tailnet",       "M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18"),
    ("audit",     "/audit",    "Audit",   "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"),
    ("actions",   "/actions",  "MCP Actions",   "M13 10V3L4 14h7v7l9-11h-7z"),
    ("posture",   "/posture",  "Posture", "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"),
    ("signins",   "/signins",  "Sign-ins",      "M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1"),
    ("users",     "/users",    "Users",         "M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"),
]

STYLES = """
:root {
  --bg: #0d1117; --surface: #161b22; --surface2: #21262d;
  --border: #30363d; --text: #e6edf3; --muted: #7d8590;
  --blue: #4493f8; --green: #3fb950; --red: #f85149;
  --amber: #d29922; --purple: #bc8cff;
  --sidebar-w: 220px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: var(--bg); color: var(--text); display: flex; min-height: 100vh; font-size: 14px; }
a { color: inherit; text-decoration: none; }

/* Sidebar */
.sidebar { width: var(--sidebar-w); background: var(--surface); border-right: 1px solid var(--border);
           display: flex; flex-direction: column; position: fixed; top: 0; left: 0; height: 100vh; z-index: 10; }
.sidebar-brand { padding: 1.25rem 1rem; border-bottom: 1px solid var(--border); }
.sidebar-brand h1 { font-size: 1rem; font-weight: 600; }
.sidebar-brand p  { font-size: 0.75rem; color: var(--muted); margin-top: 2px; }
.nav-item { display: flex; align-items: center; gap: 0.625rem; padding: 0.6rem 1rem;
            color: var(--muted); font-size: 0.875rem; border-radius: 6px; margin: 2px 0.5rem;
            transition: background 0.15s, color 0.15s; }
.nav-item:hover { background: var(--surface2); color: var(--text); }
.nav-item.active { background: rgba(68,147,248,0.15); color: var(--blue); }
.nav-item svg { width: 16px; height: 16px; flex-shrink: 0; }
.sidebar-footer { margin-top: auto; padding: 1rem; font-size: 0.7rem; color: var(--muted); border-top: 1px solid var(--border); }

/* Main */
.main { margin-left: var(--sidebar-w); flex: 1; padding: 2rem; overflow-x: hidden; }
.page-title { font-size: 1.25rem; font-weight: 600; margin-bottom: 0.25rem; }
.page-sub   { color: var(--muted); font-size: 0.8rem; margin-bottom: 1.75rem; }

/* Stat cards */
.stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 1rem; margin-bottom: 1.75rem; }
.stat-card  { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.25rem; }
.stat-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin-bottom: 0.4rem; }
.stat-value { font-size: 1.75rem; font-weight: 700; line-height: 1; }
.stat-sub   { font-size: 0.75rem; color: var(--muted); margin-top: 0.35rem; }
.c-green  { color: var(--green); }
.c-red    { color: var(--red); }
.c-blue   { color: var(--blue); }
.c-amber  { color: var(--amber); }
.c-purple { color: var(--purple); }

/* Charts row */
.charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.75rem; }
.chart-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; }
.chart-card h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-bottom: 1rem; }
.chart-wrap { position: relative; height: 180px; }

/* Table */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 1.75rem; }
.card-header { padding: 0.875rem 1.25rem; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
.card-header h3 { font-size: 0.875rem; font-weight: 600; }
table { width: 100%; border-collapse: collapse; }
th { padding: 0.6rem 1rem; text-align: left; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); background: var(--surface2); border-bottom: 1px solid var(--border); }
td { padding: 0.7rem 1rem; border-bottom: 1px solid var(--border); font-size: 0.825rem; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--surface2); }

/* Badges */
.badge { display: inline-flex; align-items: center; padding: 0.2rem 0.55rem; border-radius: 20px; font-size: 0.7rem; font-weight: 600; }
.badge-add    { background: rgba(63,185,80,.15);  color: var(--green); }
.badge-update { background: rgba(68,147,248,.15); color: var(--blue); }
.badge-delete { background: rgba(248,81,73,.15);  color: var(--red); }
.badge-other  { background: var(--surface2);      color: var(--muted); }
.badge-online  { background: rgba(63,185,80,.15);  color: var(--green); }
.badge-offline { background: rgba(248,81,73,.15);  color: var(--red); }

/* Filters */
.filters { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1rem; }
.filter-chip { padding: 0.3rem 0.75rem; border-radius: 20px; font-size: 0.75rem; border: 1px solid var(--border);
               background: var(--surface2); color: var(--muted); cursor: pointer; transition: all 0.15s; }
.filter-chip:hover, .filter-chip.active { background: rgba(68,147,248,.2); color: var(--blue); border-color: var(--blue); }

/* Empty */
.empty { padding: 3rem; text-align: center; color: var(--muted); }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.dot-green { background: var(--green); }
.dot-red   { background: var(--red); box-shadow: none; }
/* ── Mobile (bottom nav) ──────────────────────────────────────────────────── */
@media(max-width:768px){
  .sidebar {
    width:100%; height:auto; position:fixed; bottom:0; top:auto; left:0; right:0;
    flex-direction:row; border-right:none; border-top:1px solid var(--border);
    z-index:100; overflow-x:auto; -webkit-overflow-scrolling:touch;
    background:var(--surface);
  }
  .sidebar-brand,.sidebar-footer { display:none; }
  .nav-item {
    flex-direction:column; gap:0.2rem; padding:0.5rem 0.6rem;
    font-size:0.6rem; border-radius:0; margin:0;
    min-width:56px; text-align:center; white-space:nowrap; flex-shrink:0;
  }
  .nav-item svg { width:20px; height:20px; }
  .main { margin-left:0; padding:1rem; padding-bottom:72px; }
  .charts-row { grid-template-columns:1fr; }
  .stats-grid { grid-template-columns:repeat(2,1fr); }
  .three-col  { grid-template-columns:1fr; }
  table { display:block; overflow-x:auto; -webkit-overflow-scrolling:touch; }
  .page-title { font-size:1.1rem; }
  .stat-value { font-size:1.5rem; }
  .stat-span2 { grid-column: span 2; }
  .breakdown-row { font-size:0.8rem; }
}
"""

def svg_icon(path):
    return f'<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="{path}"/></svg>'

def shell(active, title, subtitle, content, extra_head=""):
    nav_html = ""
    for key, href, label, icon_path in NAV_ITEMS:
        cls = " active" if key == active else ""
        nav_html += f'<a href="{href}" class="nav-item{cls}">{svg_icon(icon_path)}{label}</a>'
    now = datetime.now(ZoneInfo("Europe/London")).strftime("%d/%m/%Y %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>{title} · Homelab</title>
  <style>{STYLES}</style>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
  {extra_head}
</head>
<body>
<aside class="sidebar">
  <div class="sidebar-brand">
    <h1>🏠 Homelab</h1>
    <p>{TAILSCALE_TAILNET}</p>
  </div>
  {nav_html}
  <div class="sidebar-footer">Updated {now}</div>
</aside>
<main class="main">
  <div class="page-title">{title}</div>
  <div class="page-sub">{subtitle}</div>
  {content}
</main>
</body>
</html>"""

# ── Overview ──────────────────────────────────────────────────────────────────

async def overview(request):
    try:
        devices = get_devices()
        online  = sum(1 for d in devices if d.get("connectedToControl"))
        offline = len(devices) - online
    except:
        devices, online, offline = [], 0, 0

    try:
        users_data = graph_get("/users?$select=displayName,userPrincipalName,accountEnabled&$filter=accountEnabled eq true")
        users = users_data.get("value", [])
        total_users = len(users)
    except:
        users, total_users = [], 0

    sign_ins = []
    try:
        logs = graph_get("/auditLogs/signIns?$top=200&$select=userPrincipalName,createdDateTime,status")
        sign_ins = logs.get("value", [])
        failed   = sum(1 for e in sign_ins if e.get("status",{}).get("errorCode",0) != 0)
        failed_pct = round(failed/len(sign_ins)*100) if sign_ins else 0
    except:
        failed, failed_pct = 0, 0

    try:
        cutoff7 = (datetime.now(timezone.utc)-timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        audit   = graph_get(f"/auditLogs/directoryAudits?$filter=activityDateTime ge {cutoff7}&$top=100&$select=activityDateTime,result")
        events  = audit.get("value", [])
        audit_count = len(events)
        audit_fail  = sum(1 for e in events if e.get("result") != "success")
    except:
        audit_count, audit_fail = 0, 0

    # MCP actions today
    today = datetime.now(timezone.utc).date()
    action_count = 0
    if os.path.exists(ACTION_LOG):
        for line in open(ACTION_LOG):
            try:
                e = json.loads(line)
                if datetime.fromisoformat(e["ts"]).date() == today:
                    action_count += 1
            except: pass

    cards = [
        ("Tailnet Devices", len(devices), f"{online} online · {offline} offline", "c-blue"),
        ("Online Now",      online,       "connected to control",                 "c-green"),
        ("Offline",         offline,      "not connected",                        "c-red" if offline else "c-muted"),
        ("Entra Users",     total_users,  "enabled accounts",                     "c-blue"),
        ("Failed Sign-ins", failed,       f"{failed_pct}% of last {len(sign_ins)} attempts",  "c-amber" if failed else "c-green"),
        ("Audit Events",    audit_count,  f"last 7 days · {audit_fail} failures", "c-purple"),
        ("MCP Actions",     action_count, "tool calls today",                     "c-blue"),
    ]
    stat_html = '<div class="stats-grid">'
    for i, (label, val, sub, cls) in enumerate(cards):
        span = ' stat-span2' if i == len(cards) - 1 and len(cards) % 2 == 1 else ''
        stat_html += f'<div class="stat-card{span}"><div class="stat-label">{label}</div><div class="stat-value {cls}">{val}</div><div class="stat-sub">{sub}</div></div>'
    stat_html += '</div>'

    # Device status donut data
    donut_data = f"[{online},{offline}]"

    # Sign-in trend: group by hour (last 24h)
    try:
        buckets = defaultdict(lambda: [0,0])  # hour -> [success, fail]
        for e in sign_ins:
            dt = datetime.fromisoformat(e.get("createdDateTime","").replace("Z","+00:00"))
            h  = dt.strftime("%H:00")
            if e.get("status",{}).get("errorCode",0) == 0: buckets[h][0] += 1
            else: buckets[h][1] += 1
        hours   = sorted(buckets.keys())[-12:]
        s_ok    = [buckets[h][0] for h in hours]
        s_fail  = [buckets[h][1] for h in hours]
        hours_j = json.dumps(hours)
        ok_j    = json.dumps(s_ok)
        fail_j  = json.dumps(s_fail)
    except:
        hours_j, ok_j, fail_j = "[]","[]","[]"

    # Security summary data
    try:
        auth_data = graph_get("/reports/authenticationMethods/userRegistrationDetails?$select=userPrincipalName,isMfaRegistered")
        auth_users = auth_data.get("value", [])
        try:
            usr_data = graph_get("/users?$select=userPrincipalName,accountEnabled&$top=200")
            disabled_upns = {u["userPrincipalName"] for u in usr_data.get("value",[]) if not u.get("accountEnabled", True)}
        except:
            disabled_upns = set()
        mfa_at_risk = sum(1 for u in auth_users if not u.get("isMfaRegistered", False) and u.get("userPrincipalName","") not in disabled_upns)
    except:
        mfa_at_risk = 0

    try:
        ca_data = graph_get("/identity/conditionalAccess/policies?$select=state")
        ca_all = ca_data.get("value", [])
        ca_enabled = sum(1 for p in ca_all if p.get("state") == "enabled")
        ca_ro = sum(1 for p in ca_all if p.get("state") == "enabledForReportingButNotEnforced")
        ca_total = len(ca_all)
    except:
        ca_enabled, ca_ro, ca_total = 0, 0, 0

    posture_score = 0
    if ca_enabled >= 3: posture_score += 40
    elif ca_enabled >= 1: posture_score += 20
    if mfa_at_risk == 0: posture_score += 40
    elif mfa_at_risk <= 2: posture_score += 20
    if failed_pct < 5: posture_score += 20
    score_col = "c-green" if posture_score >= 80 else "c-amber" if posture_score >= 50 else "c-red"
    mfa_col = "c-green" if mfa_at_risk == 0 else "c-amber" if mfa_at_risk <= 2 else "c-red"
    ca_col  = "c-green" if ca_ro == 0 else "c-amber"

    security_summary = f"""<div class="stats-grid" style="margin-bottom:1.5rem">
  <a href="/posture" class="stat-card" style="text-decoration:none;cursor:pointer">
    <div class="stat-label">Posture Score</div>
    <div class="stat-value {score_col}">{posture_score}<span style="font-size:.9rem;color:var(--muted)">/100</span></div>
    <div class="stat-sub">click to review</div>
  </a>
  <a href="/users?mfa=no&amp;status=enabled" class="stat-card" style="text-decoration:none;cursor:pointer">
    <div class="stat-label">MFA At-Risk</div>
    <div class="stat-value {mfa_col}">{mfa_at_risk}</div>
    <div class="stat-sub">enabled users without MFA</div>
  </a>
  <a href="/posture" class="stat-card" style="text-decoration:none;cursor:pointer">
    <div class="stat-label">CA Policies</div>
    <div class="stat-value {ca_col}">{ca_enabled} <span style="font-size:.9rem;color:var(--muted)">/ {ca_total}</span></div>
    <div class="stat-sub">{ca_ro} report-only</div>
  </a>
</div>
"""

    charts = f"""<div class="charts-row">
  <div class="chart-card">
    <h3>Device Status</h3>
    <div class="chart-wrap"><canvas id="donutChart"></canvas></div>
  </div>
  <div class="chart-card">
    <h3>Sign-in Activity (last 12h)</h3>
    <div class="chart-wrap"><canvas id="signInChart"></canvas></div>
  </div>
</div>
<script>
const chartDefaults = {{ responsive:true, maintainAspectRatio:false,
  plugins:{{ legend:{{ labels:{{ color:'#7d8590', font:{{ size:11 }} }} }} }},
  scales:{{ x:{{ ticks:{{ color:'#7d8590', font:{{size:10}} }}, grid:{{ color:'#21262d' }} }},
            y:{{ ticks:{{ color:'#7d8590', font:{{size:10}} }}, grid:{{ color:'#21262d' }} }} }} }};
new Chart(document.getElementById('donutChart'), {{
  type:'doughnut', data:{{
    labels:['Online','Offline'],
    datasets:[{{ data:{donut_data}, backgroundColor:['#3fb950','#f85149'], borderWidth:0 }}]
  }},
  options:{{ responsive:true, maintainAspectRatio:false, cutout:'70%',
    plugins:{{ legend:{{ position:'bottom', labels:{{ color:'#7d8590', font:{{size:11}} }} }} }} }}
}});
new Chart(document.getElementById('signInChart'), {{
  type:'bar', data:{{
    labels:{hours_j},
    datasets:[
      {{ label:'Success', data:{ok_j},   backgroundColor:'rgba(63,185,80,.6)',  borderRadius:3 }},
      {{ label:'Failed',  data:{fail_j}, backgroundColor:'rgba(248,81,73,.6)', borderRadius:3 }}
    ]
  }},
  options:{{ ...chartDefaults, plugins:{{ ...chartDefaults.plugins }}, scales:{{ x:{{ ...chartDefaults.scales.x, stacked:true }}, y:{{ ...chartDefaults.scales.y, stacked:true }} }} }}
}});
</script>"""

    return HTMLResponse(shell("overview", "Overview", "At-a-glance status across your homelab", stat_html + security_summary + charts))

# ── Tailnet ───────────────────────────────────────────────────────────────────

async def tailnet(request):
    try:
        devices = get_devices()
        online  = sum(1 for d in devices if d.get("connectedToControl"))
        offline = len(devices) - online
        rows = ""
        for d in devices:
            up = d.get("connectedToControl", False)
            dot = f'<span class="dot {"dot-green" if up else "dot-red"}"></span>'
            badge_cls = "badge-online" if up else "badge-offline"
            badge_txt = "Online" if up else "Offline"
            rows += f"""<tr>
              <td>{dot}{d['name'].split('.')[0]}</td>
              <td style="font-family:monospace;color:var(--muted)">{d['addresses'][0]}</td>
              <td>{d.get('os','—')}</td>
              <td>{d.get('clientVersion','—')}</td>
              <td>{time_ago(d.get('lastSeen',''))}</td>
              <td><span class="badge {badge_cls}">{badge_txt}</span></td>
            </tr>"""

        stats = f"""<div class="stats-grid" style="grid-template-columns:repeat(3,1fr);max-width:500px">
          <div class="stat-card"><div class="stat-label">Total</div><div class="stat-value c-blue">{len(devices)}</div></div>
          <div class="stat-card"><div class="stat-label">Online</div><div class="stat-value c-green">{online}</div></div>
          <div class="stat-card"><div class="stat-label">Offline</div><div class="stat-value {'c-red' if offline else 'c-muted'}">{offline}</div></div>
        </div>"""

        table = f"""<div class="card">
          <div class="card-header"><h3>Devices</h3></div>
          <table>
            <thead><tr><th>Device</th><th>IP</th><th>OS</th><th>Version</th><th>Last Seen</th><th>Status</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""
        content = stats + table
    except Exception as e:
        content = f'<div class="empty">Error: {e}</div>'

    return HTMLResponse(shell("tailnet", "Tailnet", "Devices in your tailnet", content, extra_head='<meta http-equiv="refresh" content="30">'))

# ── Entra Audit ───────────────────────────────────────────────────────────────

async def audit(request):
    days     = int(request.query_params.get("days", 7))
    cat_filt = request.query_params.get("cat", "")
    res_filt = request.query_params.get("result", "")

    try:
        cutoff = (datetime.now(timezone.utc)-timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data   = graph_get(f"/auditLogs/directoryAudits?$filter=activityDateTime ge {cutoff}&$top=200&$select=activityDateTime,activityDisplayName,category,result,initiatedBy,targetResources")
        entries = data.get("value", [])

        # Category counts for filter chips
        cats = defaultdict(int)
        for e in entries:
            cats[e.get("category","Other")] += 1

        # Apply filters first so chart reflects selected category
        filtered = entries
        if cat_filt:
            filtered = [e for e in filtered if e.get("category","") == cat_filt]
        if res_filt:
            filtered = [e for e in filtered if e.get("result","") == res_filt]

        # Day-by-day chart data (uses filtered set so chart matches table)
        day_buckets = defaultdict(lambda: [0,0])
        for e in filtered:
            d = e.get("activityDateTime","")[:10]
            if e.get("result") == "success": day_buckets[d][0] += 1
            else: day_buckets[d][1] += 1
        days_list = sorted(day_buckets.keys())
        ok_counts   = [day_buckets[d][0] for d in days_list]
        fail_counts = [day_buckets[d][1] for d in days_list]

        # Build rows
        rows = ""
        for e in filtered:
            action = e.get("activityDisplayName","—")
            cat    = e.get("category","—")
            result = e.get("result","—")
            ri     = "✅" if result == "success" else "❌"
            initiated = e.get("initiatedBy",{})
            actor = "—"
            if initiated.get("user"): actor = initiated["user"].get("userPrincipalName") or initiated["user"].get("displayName","user")
            elif initiated.get("app"): actor = f"🤖 {initiated['app'].get('displayName','app')}"
            targets = e.get("targetResources",[])
            target  = (targets[0].get("userPrincipalName") or targets[0].get("displayName","—")) if targets else "—"
            op = action.lower()
            if "add" in op or "create" in op: badge = '<span class="badge badge-add">ADD</span>'
            elif "update" in op or "change" in op or "reset" in op: badge = '<span class="badge badge-update">UPDATE</span>'
            elif "delete" in op or "remove" in op or "disable" in op: badge = '<span class="badge badge-delete">DELETE</span>'
            else: badge = '<span class="badge badge-other">OTHER</span>'
            rows += f"""<tr>
              <td style="color:var(--muted);white-space:nowrap">{fmt_dt(e.get('activityDateTime',''))}</td>
              <td>{badge} {action}</td>
              <td style="color:var(--muted)">{cat}</td>
              <td>{actor}</td>
              <td style="color:var(--muted)">{target}</td>
              <td>{ri} {result}</td>
            </tr>"""

        # Filter chips — query-string values URL-encoded, displayed text HTML-escaped
        cat_filt_q = quote(cat_filt, safe="")
        res_filt_q = quote(res_filt, safe="")
        day_chips = "".join([f'<a href="?days={d}{"&cat="+cat_filt_q if cat_filt else ""}{"&result="+res_filt_q if res_filt else ""}" class="filter-chip {"active" if days==d else ""}">{d}d</a>' for d in [1,7,14,30]])
        cat_chips = f'<a href="?days={days}{"&result="+res_filt_q if res_filt else ""}" class="filter-chip {"active" if not cat_filt else ""}">All</a>'
        for cat_name, cnt in sorted(cats.items(), key=lambda x: -x[1]):
            active = "active" if cat_filt == cat_name else ""
            cat_chips += f'<a href="?days={days}&cat={quote(cat_name, safe="")}{"&result="+res_filt_q if res_filt else ""}" class="filter-chip {active}">{escape(cat_name)} <span style="opacity:.6">({cnt})</span></a>'
        res_chips = "".join([
            f'<a href="?days={days}{"&cat="+cat_filt_q if cat_filt else ""}{"&result=" if r else ""}{r}" class="filter-chip {"active" if res_filt==r else ""}">{l}</a>'
            for r, l in [("","All"),("success","✅ Success"),("failure","❌ Failure")]
        ])

        chart_html = f"""<div class="chart-card" style="margin-bottom:1.5rem">
          <h3>Events per day</h3>
          <div class="chart-wrap"><canvas id="auditChart"></canvas></div>
        </div>
        <script>
        new Chart(document.getElementById('auditChart'),{{
          type:'bar', data:{{
            labels:{json.dumps(days_list)},
            datasets:[
              {{label:'Success',data:{json.dumps(ok_counts)},  backgroundColor:'rgba(63,185,80,.6)',borderRadius:3}},
              {{label:'Failure',data:{json.dumps(fail_counts)},backgroundColor:'rgba(248,81,73,.6)',borderRadius:3}}
            ]
          }},
          options:{{responsive:true,maintainAspectRatio:false,
            plugins:{{legend:{{labels:{{color:'#7d8590',font:{{size:11}}}}}}}},
            scales:{{
              x:{{stacked:true,ticks:{{color:'#7d8590',font:{{size:10}}}},grid:{{color:'#21262d'}}}},
              y:{{stacked:true,ticks:{{color:'#7d8590',font:{{size:10}}}},grid:{{color:'#21262d'}}}}
            }}
          }}
        }});
        </script>"""

        content = f"""
        {chart_html}
        <div style="margin-bottom:0.75rem">
          <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:0.4rem">Period</div>
          <div class="filters">{day_chips}</div>
        </div>
        <div style="margin-bottom:0.75rem">
          <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:0.4rem">Category</div>
          <div class="filters">{cat_chips}</div>
        </div>
        <div style="margin-bottom:1.25rem">
          <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:0.4rem">Result</div>
          <div class="filters">{res_chips}</div>
        </div>
        <div class="card">
          <div class="card-header"><h3>Events <span style="color:var(--muted);font-weight:400">({len(filtered)} shown)</span></h3></div>
          {'<table><thead><tr><th>When</th><th>Action</th><th>Category</th><th>Actor</th><th>Target</th><th>Result</th></tr></thead><tbody>'+rows+'</tbody></table>' if rows else '<div class="empty">No events match these filters.</div>'}
        </div>"""

    except Exception as e:
        content = f'<div class="empty">Error: {e}</div>'

    return HTMLResponse(shell("audit", "Audit", f"Directory audit log · last {days} days", content))

# ── MCP Actions ───────────────────────────────────────────────────────────────

async def actions(request):
    days = int(request.query_params.get("days", 7))
    server_filt = request.query_params.get("server", "")

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        entries = []
        if os.path.exists(ACTION_LOG):
            for line in open(ACTION_LOG):
                try:
                    e = json.loads(line.strip())
                    if datetime.fromisoformat(e["ts"]) >= cutoff:
                        entries.append(e)
                except: pass

        entries.sort(key=lambda e: e["ts"], reverse=True)

        # Tool breakdown for chart
        tool_counts = defaultdict(int)
        server_counts = defaultdict(int)
        for e in entries:
            tool_counts[e.get("tool","?")] += 1
            server_counts[e.get("server","?")] += 1

        top_tools   = sorted(tool_counts.items(), key=lambda x: -x[1])[:8]
        tool_labels = json.dumps([t[0] for t in top_tools])
        tool_vals   = json.dumps([t[1] for t in top_tools])
        COLORS = ["#4493f8","#3fb950","#bc8cff","#d29922","#f85149","#58a6ff","#56d364","#e3b341"]
        tool_colors = json.dumps(COLORS[:len(top_tools)])

        # Server chips
        servers = list(server_counts.keys())
        srv_chips = f'<a href="?days={days}" class="filter-chip {"active" if not server_filt else ""}">All</a>'
        for srv in servers:
            active = "active" if server_filt == srv else ""
            srv_chips += f'<a href="?days={days}&server={srv}" class="filter-chip {active}">{srv} <span style="opacity:.6">({server_counts[srv]})</span></a>'

        day_chips = "".join([f'<a href="?days={d}{"&server="+server_filt if server_filt else ""}" class="filter-chip {"active" if days==d else ""}">{d}d</a>' for d in [1,7,14,30]])

        # Filter
        filtered = [e for e in entries if not server_filt or e.get("server") == server_filt]

        rows = ""
        for e in filtered[:200]:
            dt   = datetime.fromisoformat(e["ts"]).strftime("%d/%m %H:%M:%S")
            tool = e.get("tool","—")
            srv  = e.get("server","—")
            raw_args = json.dumps(e.get("args",{}), ensure_ascii=False)
            args = (raw_args[:80] + "…") if len(raw_args) > 80 else raw_args
            srv_color = "var(--blue)" if "tailscale" in srv else "var(--purple)"
            rows += f"""<tr>
              <td style="color:var(--muted);white-space:nowrap">{dt}</td>
              <td>⚡ {tool}</td>
              <td style="color:{srv_color}">{srv}</td>
              <td style="font-family:monospace;font-size:0.75rem;color:var(--muted)">{args}</td>
            </tr>"""

        chart_html = f"""<div class="chart-card" style="margin-bottom:1.5rem">
          <h3>Top tools used</h3>
          <div class="chart-wrap"><canvas id="toolChart"></canvas></div>
        </div>
        <script>
        new Chart(document.getElementById('toolChart'),{{
          type:'bar', data:{{
            labels:{tool_labels},
            datasets:[{{label:'Calls',data:{tool_vals},backgroundColor:{tool_colors},borderRadius:4}}]
          }},
          options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
            plugins:{{legend:{{display:false}}}},
            scales:{{
              x:{{ticks:{{color:'#7d8590',font:{{size:10}}}},grid:{{color:'#21262d'}}}},
              y:{{ticks:{{color:'#e6edf3',font:{{size:11}}}},grid:{{display:false}}}}
            }}
          }}
        }});
        </script>"""

        stats = f"""<div class="stats-grid" style="grid-template-columns:repeat(3,1fr);max-width:500px;margin-bottom:1.5rem">
          <div class="stat-card"><div class="stat-label">Total Actions</div><div class="stat-value c-blue">{len(entries)}</div></div>
          <div class="stat-card"><div class="stat-label">Unique Tools</div><div class="stat-value c-purple">{len(tool_counts)}</div></div>
          <div class="stat-card"><div class="stat-label">Servers</div><div class="stat-value c-green">{len(server_counts)}</div></div>
        </div>"""

        content = f"""
        {stats}
        {chart_html}
        <div style="margin-bottom:.75rem">
          <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:.4rem">Period</div>
          <div class="filters">{day_chips}</div>
        </div>
        <div style="margin-bottom:1.25rem">
          <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:.4rem">Server</div>
          <div class="filters">{srv_chips}</div>
        </div>
        <div class="card">
          <div class="card-header"><h3>Action Log <span style="color:var(--muted);font-weight:400">({len(filtered)} shown)</span></h3></div>
          {'<table><thead><tr><th>When</th><th>Tool</th><th>Server</th><th>Args</th></tr></thead><tbody>'+rows+'</tbody></table>' if rows else '<div class="empty">No actions logged for this period.</div>'}
        </div>"""

    except Exception as e:
        content = f'<div class="empty">Error: {e}</div>'

    return HTMLResponse(shell("actions", "MCP Actions", f"Tool call audit log · last {days} days", content))


# ── Security Posture ──────────────────────────────────────────────────────────────

async def posture(request):
    try:
        devices = get_devices()
        online  = sum(1 for d in devices if d.get("connectedToControl"))
        offline = len(devices) - online
    except:
        devices, online, offline = [], 0, 0

    try:
        ca_data     = graph_get("/identity/conditionalAccess/policies?$select=displayName,state")
        ca_policies = ca_data.get("value", [])
        report_only = [p for p in ca_policies if p.get("state") == "enabledForReportingButNotEnforced"]
        enabled_ca  = [p for p in ca_policies if p.get("state") == "enabled"]
    except:
        ca_policies, report_only, enabled_ca = [], [], []

    try:
        auth_data  = graph_get("/reports/authenticationMethods/userRegistrationDetails?$select=userPrincipalName,displayName,isMfaRegistered,methodsRegistered,isAdmin")
        auth_users = auth_data.get("value", [])
        # Get enabled accounts to exclude disabled ones from MFA gap scoring
        try:
            usr_data = graph_get("/users?$select=userPrincipalName,accountEnabled&$top=200")
            disabled_upns = {u["userPrincipalName"] for u in usr_data.get("value",[]) if not u.get("accountEnabled", True)}
        except:
            disabled_upns = set()
        mfa_gaps = [u for u in auth_users if not u.get("isMfaRegistered", False) and u.get("userPrincipalName","") not in disabled_upns]
    except:
        auth_users, mfa_gaps, disabled_upns = [], [], set()

    sign_ins_p, failed_si, fail_rate = [], 0, 0
    try:
        sl = graph_get("/auditLogs/signIns?$top=200&$select=status")
        sign_ins_p = sl.get("value", [])
        failed_si  = sum(1 for e in sign_ins_p if e.get("status", {}).get("errorCode", 0) != 0)
        fail_rate  = round(failed_si / len(sign_ins_p) * 100) if sign_ins_p else 0
    except:
        pass

    # Score
    score, deductions = 100, []
    ro_ded = min(len(report_only) * 8, 40)
    if ro_ded:
        score -= ro_ded
        deductions.append(f"{len(report_only)} report-only CA polic{'y' if len(report_only)==1 else 'ies'} (-{ro_ded})")
    mfa_ded = min(len(mfa_gaps) * 8, 32)
    if mfa_ded:
        score -= mfa_ded
        deductions.append(f"{len(mfa_gaps)} user{'s' if len(mfa_gaps)!=1 else ''} without MFA (-{mfa_ded})")
    if fail_rate > 10:
        score -= 10; deductions.append(f"High sign-in failure rate ({fail_rate}%) (-10)")
    elif fail_rate > 5:
        score -= 5;  deductions.append(f"Elevated sign-in failure rate ({fail_rate}%) (-5)")
    off_ded = min(offline * 2, 10)
    if off_ded:
        score -= off_ded
        deductions.append(f"{offline} offline Tailscale node{'s' if offline!=1 else ''} (-{off_ded})")
    score = max(0, score)

    if score >= 90:   score_cls, score_label = "c-green", "Good — minor improvements recommended"
    elif score >= 70: score_cls, score_label = "c-amber", "Fair — some issues need attention"
    elif score >= 50: score_cls, score_label = "c-red",   "Poor — action required"
    else:             score_cls, score_label = "c-red",   "Critical — immediate action needed"

    ded_items = "".join(
        f'<li style="margin:.3rem 0;color:var(--muted);font-size:.8rem">⚠️ {d}</li>'
        for d in deductions
    ) or '<li style="color:var(--green);font-size:.8rem">✅ No deductions — looking good</li>'

    score_card = (
        '<div style="display:grid;grid-template-columns:140px 1fr;gap:1.5rem;align-items:center;'
        'background:var(--surface);border:1px solid var(--border);border-radius:8px;'
        'padding:1.75rem;margin-bottom:1.75rem">'
        f'<div style="text-align:center"><div class="{score_cls}" style="font-size:4.5rem;font-weight:700;line-height:1">{score}</div>'
        '<div style="font-size:.75rem;color:var(--muted);margin-top:.25rem">/ 100</div></div>'
        f'<div><div style="font-size:1rem;font-weight:600;margin-bottom:.75rem">{score_label}</div>'
        f'<ul style="list-style:none;padding:0">{ded_items}</ul></div>'
        '</div>'
    )

    covered = len(auth_users) - len(mfa_gaps)
    ca_c  = "c-green" if not report_only else ("c-amber" if len(report_only) <= 2 else "c-red")
    mfa_c = "c-green" if not mfa_gaps   else ("c-amber" if len(mfa_gaps) <= 2   else "c-red")
    si_c  = "c-green" if fail_rate <= 5 else ("c-amber" if fail_rate <= 10      else "c-red")
    off_c = "c-green" if not offline    else ("c-amber" if offline <= 1         else "c-red")

    factor_cards = (
        '<div class="stats-grid" style="margin-bottom:1.75rem">'
        f'<div class="stat-card"><div class="stat-label">Report-only Policies</div><div class="stat-value {ca_c}">{len(report_only)}</div><div class="stat-sub">{len(enabled_ca)} enforced · {len(ca_policies)} total</div></div>'
        f'<div class="stat-card"><div class="stat-label">MFA Coverage</div><div class="stat-value {mfa_c}">{covered}/{len(auth_users)}</div><div class="stat-sub">{len(mfa_gaps)} gap{"s" if len(mfa_gaps)!=1 else ""} found</div></div>'
        f'<div class="stat-card"><div class="stat-label">Sign-in Failure Rate</div><div class="stat-value {si_c}">{fail_rate}%</div><div class="stat-sub">{failed_si} of {len(sign_ins_p)} recent attempts</div></div>'
        f'<div class="stat-card"><div class="stat-label">Offline Nodes</div><div class="stat-value {off_c}">{offline}</div><div class="stat-sub">{online} of {len(devices)} online</div></div>'
        '</div>'
    )

    ca_rows = ""
    for p in sorted(ca_policies, key=lambda x: (x.get("state") != "enabled", x.get("displayName",""))):
        st = p.get("state", "")
        if st == "enabled":
            badge = '<span class="badge badge-add">ENFORCED</span>'
        elif st == "enabledForReportingButNotEnforced":
            badge = '<span class="badge badge-update">REPORT-ONLY</span>'
        else:
            badge = '<span class="badge badge-delete">DISABLED</span>'
        ca_rows += f'<tr><td>{p.get("displayName","--")}</td><td>{badge}</td></tr>'

    ca_table = (
        '<div class="card" style="margin-bottom:1.75rem">'
        f'<div class="card-header"><h3>Conditional Access Policies <span style="color:var(--muted);font-weight:400">({len(ca_policies)} total)</span></h3></div>'
        f'<table><thead><tr><th>Policy Name</th><th>State</th></tr></thead><tbody>{ca_rows}</tbody></table>'
        '</div>'
    )

    METHODS = {
        "microsoftAuthenticatorPush":         "📱 Authenticator",
        "softwareOneTimePasscode":            "🔢 TOTP",
        "fido2SecurityKey":                   "🔑 FIDO2",
        "windowsHelloForBusiness":            "🪟 Windows Hello",
        "mobilePhone":                        "📞 Phone",
        "email":                              "📧 Email",
        "microsoftAuthenticatorPasswordless": "✨ Passwordless",
        "temporaryAccessPass":                "⏱️ TAP",
    }

    mfa_rows = ""
    for u in sorted(auth_users, key=lambda x: (x.get("isMfaRegistered", False), x.get("displayName",""))):
        methods    = u.get("methodsRegistered", [])
        method_str = ", ".join(METHODS.get(m, m) for m in methods) or "—"
        is_gap     = not u.get("isMfaRegistered", False)
        gap_cell   = '<span class="badge badge-delete">GAP</span>' if is_gap else '<span class="badge badge-add">MFA ✓</span>'
        row_style  = ' style="background:rgba(248,81,73,.05)"' if is_gap else ""
        name       = u.get("displayName") or u.get("userPrincipalName", "—")
        upn        = u.get("userPrincipalName", "—")
        mfa_rows  += f'<tr{row_style}><td>{name}</td><td style="color:var(--muted);font-size:.8rem">{upn}</td><td style="font-size:.8rem">{method_str}</td><td>{gap_cell}</td></tr>'

    if mfa_rows:
        mfa_inner = ('<table><thead><tr><th>Name</th><th>UPN</th><th>Registered Methods</th><th>MFA</th></tr></thead>'
                     '<tbody>' + mfa_rows + '</tbody></table>')
    else:
        mfa_inner = '<div class="empty">Unable to fetch registration data — check UserAuthenticationMethod.Read.All.</div>'

    mfa_table = (
        '<div class="card">'
        f'<div class="card-header"><h3>MFA Registration <span style="color:var(--muted);font-weight:400">({len(auth_users)} accounts)</span></h3></div>'
        + mfa_inner +
        '</div>'
    )

    return HTMLResponse(shell(
        "posture", "Posture",
        "Tenant health score and key risk indicators",
        score_card + factor_cards + ca_table + mfa_table,
        extra_head='<meta http-equiv="refresh" content="300">'
    ))


# ── Sign-ins page ─────────────────────────────────────────────────────────────

async def signins(request):
    status_filter = request.query_params.get("status", "all")

    raw = []
    try:
        data = graph_get(
            "/auditLogs/signIns?$top=200"
            "&$select=userPrincipalName,userDisplayName,createdDateTime,status,"
            "appDisplayName,clientAppUsed,deviceDetail,location,ipAddress,"
            "conditionalAccessStatus,isInteractive,riskState"
        )
        raw = data.get("value", [])
    except Exception as e:
        raw = []

    from zoneinfo import ZoneInfo
    from datetime import datetime as dt

    def fmt_time(s):
        try:
            d = dt.fromisoformat(s.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/London"))
            return d.strftime("%d/%m %H:%M")
        except:
            return s[:16]

    def is_fail(e):
        return e.get("status", {}).get("errorCode", 0) != 0

    def ca_badge(v):
        colours = {"success": "c-green", "failure": "c-red", "notApplied": "c-muted", "unknownFutureValue": "c-muted"}
        return f'<span class="{colours.get(v,"c-muted")}">{v or "—"}</span>'

    # Filter
    if status_filter == "fail":
        entries = [e for e in raw if is_fail(e)]
    elif status_filter == "success":
        entries = [e for e in raw if not is_fail(e)]
    else:
        entries = raw

    total   = len(raw)
    failed  = sum(1 for e in raw if is_fail(e))
    success = total - failed
    fail_pct = round(failed / total * 100) if total else 0

    unique_users = len({e.get("userPrincipalName","") for e in raw})

    unmanaged = sum(1 for e in raw if not e.get("deviceDetail", {}).get("isManaged", False))
    unmanaged_pct = round(unmanaged / total * 100) if total else 0

    # Failure reasons
    from collections import Counter
    fail_reasons = Counter()
    for e in raw:
        if is_fail(e):
            reason = e.get("status", {}).get("failureReason") or "Unknown"
            fail_reasons[reason] += 1

    # App breakdown
    app_counts = Counter(e.get("appDisplayName") or "Unknown" for e in raw)

    # Location breakdown
    loc_counts = Counter()
    for e in raw:
        loc = e.get("location", {})
        city = loc.get("city") or ""
        country = loc.get("countryOrRegion") or ""
        if city or country:
            loc_counts[f"{city}, {country}" if city else country] += 1

    # Hourly chart data (last 48h buckets)
    from collections import defaultdict
    hourly_ok  = defaultdict(int)
    hourly_err = defaultdict(int)
    for e in raw:
        try:
            d = dt.fromisoformat(e["createdDateTime"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/London"))
            bucket = d.strftime("%d/%m %H:00")
            if is_fail(e): hourly_err[bucket] += 1
            else:          hourly_ok[bucket]  += 1
        except:
            pass

    all_buckets = sorted(set(list(hourly_ok.keys()) + list(hourly_err.keys())))
    chart_labels = all_buckets
    chart_ok     = [hourly_ok.get(b, 0)  for b in all_buckets]
    chart_err    = [hourly_err.get(b, 0) for b in all_buckets]

    import json as _json
    chart_js = f"""
    <canvas id="siChart" style="width:100%;height:220px;max-height:220px"></canvas>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
    <script>
    new Chart(document.getElementById("siChart"),{{
      type:"bar",
      data:{{
        labels:{_json.dumps(chart_labels)},
        datasets:[
          {{label:"Success",data:{_json.dumps(chart_ok)},backgroundColor:"rgba(63,185,80,0.7)",stack:"s"}},
          {{label:"Failed", data:{_json.dumps(chart_err)},backgroundColor:"rgba(248,81,73,0.7)",stack:"s"}}
        ]
      }},
      options:{{
        responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{labels:{{color:"#e6edf3",font:{{size:12}}}}}}}},
        scales:{{
          x:{{stacked:true,ticks:{{color:"#7d8590",maxTicksLimit:12,maxRotation:45}},grid:{{color:"#21262d"}}}},
          y:{{stacked:true,ticks:{{color:"#7d8590"}},grid:{{color:"#21262d"}}}}
        }}
      }}
    }});
    </script>"""

    # Table rows
    rows_html = ""
    for e in entries[:100]:
        fail = is_fail(e)
        upn  = e.get("userPrincipalName", "—")
        name = e.get("userDisplayName") or upn.split("@")[0]
        ts   = fmt_time(e.get("createdDateTime", ""))
        app  = e.get("appDisplayName") or "—"
        client = e.get("clientAppUsed") or "—"
        dd   = e.get("deviceDetail") or {}
        os_  = dd.get("operatingSystem") or "—"
        mgd  = "✅" if dd.get("isManaged") else "⬜"
        loc  = e.get("location") or {}
        city = loc.get("city") or loc.get("countryOrRegion") or "—"
        ca   = e.get("conditionalAccessStatus") or "—"
        risk = e.get("riskState") or "none"

        status_html = '<span class="c-red">✗ Fail</span>' if fail else '<span class="c-green">✓ OK</span>'
        risk_html   = f'<span class="c-amber">{risk}</span>' if risk not in ("none","hidden","") else '<span class="c-muted">—</span>'
        err_msg = ""
        if fail:
            err_msg = f'<div class="c-muted" style="font-size:11px">{e.get("status",{}).get("failureReason","")[:60]}</div>'

        rows_html += f"""<tr>
          <td><div>{name}</div><div class="c-muted" style="font-size:11px">{upn}</div></td>
          <td class="c-muted">{ts}</td>
          <td>{app}</td>
          <td>{client} / {os_} {mgd}</td>
          <td>{city}</td>
          <td>{ca_badge(ca)}</td>
          <td>{status_html}{err_msg}</td>
          <td>{risk_html}</td>
        </tr>"""

    # Failure reasons panel
    if fail_reasons:
        items = "".join(f'<div class="breakdown-row"><span>{r}</span><span class="c-red">{c}</span></div>' for r,c in fail_reasons.most_common(8))
        fail_panel = f'<div class="card"><div class="card-title">Failure Reasons</div>{items}</div>'
    else:
        fail_panel = '<div class="card"><div class="card-title">Failure Reasons</div><div style="padding:1rem 0;text-align:center;color:var(--green)">&#10003; No failures in this period</div></div>'

    # App breakdown panel
    app_items = "".join(f'<div class="breakdown-row"><span>{a}</span><span class="c-muted">{c}</span></div>' for a,c in app_counts.most_common(8))
    app_panel = f'<div class="card"><div class="card-title">Top Apps</div>{app_items}</div>'

    # Location panel
    loc_items = "".join(f'<div class="breakdown-row"><span>{l}</span><span class="c-muted">{c}</span></div>' for l,c in loc_counts.most_common(8))
    loc_panel = f'<div class="card"><div class="card-title">Locations</div>{loc_items}</div>'

    filter_active = lambda v: "filter-btn active" if status_filter==v else "filter-btn"

    content = f"""
<div class="stat-row">
  <div class="stat-card"><div class="stat-value">{total}</div><div class="stat-label">Total Sign-ins</div><div class="stat-sub">last 200 records</div></div>
  <div class="stat-card"><div class="stat-value c-{'red' if fail_pct>5 else 'green'}">{failed}</div><div class="stat-label">Failed</div><div class="stat-sub">{fail_pct}% failure rate</div></div>
  <div class="stat-card"><div class="stat-value">{unique_users}</div><div class="stat-label">Unique Users</div><div class="stat-sub">distinct accounts</div></div>
  <div class="stat-card"><div class="stat-value c-{'amber' if unmanaged_pct>50 else 'muted'}">{unmanaged_pct}%</div><div class="stat-label">Unmanaged Devices</div><div class="stat-sub">{unmanaged} of {total} sign-ins</div></div>
</div>

<div class="card" style="margin-bottom:1.5rem">
  <div class="card-title">Sign-in Activity</div>
  {chart_js}
</div>

<div class="three-col" style="margin-bottom:1.5rem">
  {fail_panel}
  {app_panel}
  {loc_panel}
</div>

<div class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem">
    <div class="card-title" style="margin:0">Recent Sign-ins</div>
    <div style="display:flex;gap:.5rem">
      <a href="/signins?status=all"   class="{filter_active('all')}">All</a>
      <a href="/signins?status=success" class="{filter_active('success')}">Success</a>
      <a href="/signins?status=fail"  class="{filter_active('fail')}">Failed</a>
    </div>
  </div>
  <div class="table-wrap">
  <table>
    <thead><tr>
      <th>User</th><th>Time</th><th>App</th><th>Client / OS</th>
      <th>Location</th><th>CA</th><th>Status</th><th>Risk</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  </div>
</div>"""

    extra_css = """
<style>
.three-col { display:grid; grid-template-columns:repeat(3,1fr); gap:1rem; }
@media(max-width:900px){.three-col{grid-template-columns:1fr;}}
.breakdown-row { display:flex; justify-content:space-between; padding:.4rem 0;
                 border-bottom:1px solid var(--border); font-size:13px; }
.breakdown-row:last-child { border-bottom:none; }
.filter-btn { padding:.3rem .75rem; border-radius:6px; font-size:12px;
              background:var(--surface2); color:var(--muted); border:1px solid var(--border); }
.filter-btn.active { background:var(--blue); color:#fff; border-color:var(--blue); }
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; padding:.5rem .75rem; color:var(--muted);
     border-bottom:1px solid var(--border); font-weight:500; white-space:nowrap; }
td { padding:.6rem .75rem; border-bottom:1px solid var(--border); vertical-align:top; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:var(--surface2); }
</style>"""

    return HTMLResponse(shell("signins", "Sign-ins", f"{total} sign-ins · {failed} failed · {unique_users} users", content, extra_css))



# ── Users page ────────────────────────────────────────────────────────────────

async def users(request):
    status_filter = request.query_params.get("status", "all")
    mfa_filter    = request.query_params.get("mfa",    "all")

    user_list, mfa_map = [], {}

    try:
        ud = graph_get("/users?$select=displayName,userPrincipalName,accountEnabled,createdDateTime,jobTitle,department&$top=200")
        user_list = ud.get("value", [])
    except Exception as e:
        user_list = []

    try:
        md = graph_get("/reports/authenticationMethods/userRegistrationDetails?$select=userPrincipalName,isMfaRegistered,methodsRegistered,isAdmin")
        mfa_map = {u["userPrincipalName"]: u for u in md.get("value", [])}
    except:
        mfa_map = {}

    METHODS_SHORT = {
        "microsoftAuthenticatorPush":          "Authenticator",
        "microsoftAuthenticatorPasswordless":  "Passwordless",
        "softwareOneTimePasscode":             "TOTP",
        "fido2SecurityKey":                    "FIDO2",
        "windowsHelloForBusiness":             "Hello",
        "mobilePhone":                         "Phone",
        "email":                               "Email",
        "temporaryAccessPass":                 "TAP",
        "password":                            None,   # skip — not interesting
    }

    merged = []
    for u in user_list:
        upn  = u.get("userPrincipalName", "")
        mi   = mfa_map.get(upn, {})
        raw_methods = mi.get("methodsRegistered", [])
        nice_methods = [METHODS_SHORT.get(m, m) for m in raw_methods if METHODS_SHORT.get(m) is not None]
        merged.append({
            "name":    u.get("displayName") or upn.split("@")[0],
            "upn":     upn,
            "enabled": u.get("accountEnabled", True),
            "created": (u.get("createdDateTime") or "")[:10],
            "job":     u.get("jobTitle") or "",
            "dept":    u.get("department") or "",
            "mfa":     mi.get("isMfaRegistered", False),
            "methods": nice_methods,
            "is_admin": mi.get("isAdmin", False),
        })

    # Apply filters
    view = merged
    if status_filter == "enabled":  view = [u for u in view if u["enabled"]]
    if status_filter == "disabled": view = [u for u in view if not u["enabled"]]
    if mfa_filter == "yes":  view = [u for u in view if u["mfa"]]
    if mfa_filter == "no":   view = [u for u in view if not u["mfa"]]

    # Stats
    total     = len(merged)
    enabled_n = sum(1 for u in merged if u["enabled"])
    mfa_n     = sum(1 for u in merged if u["mfa"])
    risk_n    = sum(1 for u in merged if u["enabled"] and not u["mfa"])

    # Table rows
    rows = ""
    for u in view:
        status_dot  = '<span class="dot dot-green"></span>' if u["enabled"] else '<span class="dot dot-red"></span>'
        status_txt  = "Enabled" if u["enabled"] else '<span class="c-muted">Disabled</span>'
        mfa_html    = '<span class="c-green">✓ MFA</span>' if u["mfa"] else '<span class="c-red">✗ No MFA</span>'
        methods_html= f'<div class="c-muted" style="font-size:11px">{", ".join(u["methods"]) if u["methods"] else "—"}</div>'
        admin_badge = ' <span style="font-size:10px;background:var(--purple);color:#fff;padding:1px 5px;border-radius:4px">ADMIN</span>' if u["is_admin"] else ""
        rows += f"""<tr>
          <td><strong>{u["name"]}</strong>{admin_badge}<div class="c-muted" style="font-size:11px">{u["upn"]}</div></td>
          <td>{status_dot}{status_txt}</td>
          <td>{mfa_html}{methods_html}</td>
          <td class="c-muted">{u["job"] or "—"}</td>
          <td class="c-muted">{u["created"]}</td>
        </tr>"""

    fa = lambda k, v: "filter-btn active" if request.query_params.get(k) == v else "filter-btn"
    fal = lambda k: "filter-btn active" if not request.query_params.get(k) else "filter-btn"

    extra_css = """<style>
.filter-btn { padding:.3rem .75rem; border-radius:6px; font-size:12px;
              background:var(--surface2); color:var(--muted); border:1px solid var(--border); }
.filter-btn.active { background:var(--blue); color:#fff; border-color:var(--blue); }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; padding:.5rem .75rem; color:var(--muted); border-bottom:1px solid var(--border); font-weight:500; }
td { padding:.6rem .75rem; border-bottom:1px solid var(--border); vertical-align:top; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:var(--surface2); }
.dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; }
.dot-green { background:var(--green); }
.dot-red   { background:var(--red); }
</style>"""

    content = f"""
<div class="stat-row">
  <div class="stat-card"><div class="stat-value">{total}</div><div class="stat-label">Total Users</div></div>
  <div class="stat-card"><div class="stat-value c-green">{enabled_n}</div><div class="stat-label">Enabled</div><div class="stat-sub">{total-enabled_n} disabled</div></div>
  <div class="stat-card"><div class="stat-value c-green">{mfa_n}</div><div class="stat-label">MFA Registered</div><div class="stat-sub">{total-mfa_n} without</div></div>
  <div class="stat-card"><div class="stat-value c-{"red" if risk_n else "green"}">{risk_n}</div><div class="stat-label">At Risk</div><div class="stat-sub">enabled, no MFA</div></div>
</div>
<div class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.75rem;margin-bottom:1rem">
    <div class="card-title" style="margin:0">User Directory <span style="font-weight:400;color:var(--muted);font-size:.85rem">({len(view)} shown)</span></div>
    <div style="display:flex;gap:.5rem;flex-wrap:wrap">
      <a href="/users" class="{fal("status")} {fal("mfa")}">All</a>
      <a href="/users?status=enabled{("&mfa="+mfa_filter) if mfa_filter!="all" else ""}" class="{fa("status","enabled")}">Enabled</a>
      <a href="/users?status=disabled{("&mfa="+mfa_filter) if mfa_filter!="all" else ""}" class="{fa("status","disabled")}">Disabled</a>
      <span style="color:var(--border)">|</span>
      <a href="/users?mfa=yes{("&status="+status_filter) if status_filter!="all" else ""}" class="{fa("mfa","yes")}">MFA ✓</a>
      <a href="/users?mfa=no{("&status="+status_filter) if status_filter!="all" else ""}" class="{fa("mfa","no")}">No MFA ✗</a>
    </div>
  </div>
  <div style="overflow-x:auto">
  <table>
    <thead><tr><th>User</th><th>Status</th><th>MFA</th><th>Role / Dept</th><th>Created</th></tr></thead>
    <tbody>{rows if rows else '<tr><td colspan="5" class="empty">No users match these filters.</td></tr>'}</tbody>
  </table>
  </div>
</div>"""

    return HTMLResponse(shell("users", "Users", f"{enabled_n} enabled · {mfa_n} MFA registered · {risk_n} at risk", content, extra_css))


# ── App ───────────────────────────────────────────────────────────────────────

app = Starlette(routes=[
    Route("/",        endpoint=overview),
    Route("/tailnet", endpoint=tailnet),
    Route("/audit",   endpoint=audit),
    Route("/actions", endpoint=actions),
    Route("/posture", endpoint=posture),
    Route("/signins", endpoint=signins),
    Route("/users",   endpoint=users),
])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
