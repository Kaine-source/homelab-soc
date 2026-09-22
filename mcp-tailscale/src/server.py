import subprocess
import os
import logging
import httpx
import docker as docker_sdk
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("mcp-tailscale")

import json as _json
_ACTION_LOG = "/home/kaine/action.log"

def _log_action(tool: str, args: dict):
    try:
        entry = {"ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "server": "mcp-tailscale", "tool": tool, "args": args}
        with open(_ACTION_LOG, "a") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception:
        pass

load_dotenv()

TAILSCALE_API_KEY = os.getenv("TAILSCALE_API_KEY")
TAILSCALE_TAILNET = os.getenv("TAILSCALE_TAILNET")
MCP_AUTH_TOKEN    = os.getenv("MCP_AUTH_TOKEN", "")
BASE_URL          = "https://api.tailscale.com/api/v2"

mcp = FastMCP("Tailscale Monitor")

def ts_headers():
    return {"Authorization": f"Bearer {TAILSCALE_API_KEY}"}

def get_devices():
    with httpx.Client() as client:
        r = client.get(f"{BASE_URL}/tailnet/{TAILSCALE_TAILNET}/devices", headers=ts_headers())
        r.raise_for_status()
        return r.json().get("devices", [])

@mcp.tool()
def list_devices() -> str:
    """List all devices in the tailnet with their status."""
    logger.info("list_devices called")
    _log_action("list_devices", {})
    devices = get_devices()
    lines = []
    for d in devices:
        lines.append(f"- {d['name']} | IP: {d['addresses'][0]} | Last seen: {d['lastSeen']}")
    return "\n".join(lines)

@mcp.tool()
def get_offline_devices() -> str:
    """Return devices that have not been seen recently."""
    logger.info("get_offline_devices called")
    _log_action("get_offline_devices", {})
    devices = get_devices()
    offline = [d for d in devices if d.get("blocksIncomingConnections")]
    if not offline:
        return "All devices appear online."
    return "\n".join([f"- {d['name']} | Last seen: {d['lastSeen']}" for d in offline])

@mcp.tool()
def get_device(name: str) -> str:
    """Get details for a specific device by name."""
    logger.info(f"get_device called: {name}")
    _log_action("get_device", {"name": name})
    devices = get_devices()
    match = next((d for d in devices if name.lower() in d["name"].lower()), None)
    if not match:
        return f"No device found matching '{name}'."
    return (
        f"Name: {match['name']}\n"
        f"IP: {match['addresses'][0]}\n"
        f"OS: {match['os']}\n"
        f"Last seen: {match['lastSeen']}\n"
        f"Tailscale version: {match['clientVersion']}"
    )

@mcp.tool()
def run_command(command: str) -> str:
    """Run a shell command on the Raspberry Pi and return the output."""
    logger.info(f"run_command: {command}")
    _log_action("run_command", {"command": command})
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout or ""
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds"
    except Exception as e:
        logger.error(f"run_command error: {e}")
        return f"Error: {e}"

@mcp.tool()
def restart_service(service: str) -> str:
    """Restart a Docker Compose service on the Pi by name (e.g. 'mcp-tailscale', 'mcp-graph', 'dashboard').
    
    Known services: mcp-tailscale, dashboard, ntfy, alerter, mcp-graph
    """
    logger.info(f"restart_service called: {service}")
    _log_action("restart_service", {"service": service})
    # Map service name to the container name Docker Compose assigns
    container_map = {
        "mcp-tailscale": "mcp-tailscale-mcp-tailscale-1",
        "dashboard": "mcp-tailscale-dashboard-1",
        "ntfy": "mcp-tailscale-ntfy-1",
        "alerter": "mcp-tailscale-alerter-1",
        "mcp-graph": "mcp-graph-mcp-graph-1",
    }
    container_name = container_map.get(service)
    if not container_name:
        return f"Unknown service '{service}'. Known services: {', '.join(container_map.keys())}"
    try:
        client = docker_sdk.from_env()
        container = client.containers.get(container_name)
        container.restart(timeout=30)
        return f"✅ Service '{service}' restarted successfully."
    except docker_sdk.errors.NotFound:
        return f"❌ Container '{container_name}' not found. Is it running?"
    except Exception as e:
        logger.error(f"restart_service error: {e}")
        return f"Error: {e}"

@mcp.tool()
def get_logs(service: str, lines: int = 50) -> str:
    """Get recent logs for a Docker Compose service (default 50 lines).
    
    Known services: mcp-tailscale, dashboard, ntfy, alerter, mcp-graph
    """
    logger.info(f"get_logs called: {service}, lines={lines}")
    _log_action("get_logs", {"service": service, "lines": lines})
    container_map = {
        "mcp-tailscale": "mcp-tailscale-mcp-tailscale-1",
        "dashboard": "mcp-tailscale-dashboard-1",
        "ntfy": "mcp-tailscale-ntfy-1",
        "alerter": "mcp-tailscale-alerter-1",
        "mcp-graph": "mcp-graph-mcp-graph-1",
    }
    container_name = container_map.get(service)
    if not container_name:
        return f"Unknown service '{service}'. Known services: {', '.join(container_map.keys())}"
    try:
        client = docker_sdk.from_env()
        container = client.containers.get(container_name)
        logs = container.logs(tail=lines, timestamps=True).decode("utf-8", errors="replace")
        return logs.strip() or "(no logs)"
    except docker_sdk.errors.NotFound:
        return f"❌ Container '{container_name}' not found."
    except Exception as e:
        logger.error(f"get_logs error: {e}")
        return f"Error: {e}"

@mcp.tool()
def disk_usage() -> str:
    """Return disk usage for the Raspberry Pi — overall and top directories."""
    logger.info("disk_usage called")
    _log_action("disk_usage", {})
    try:
        df = subprocess.run("df -h /", shell=True, capture_output=True, text=True, timeout=10)
        du = subprocess.run(
            "du -sh /home/kaine/mcp-tailscale /home/kaine/mcp-graph 2>/dev/null",
            shell=True, capture_output=True, text=True, timeout=15
        )
        # Get Docker info via SDK
        try:
            client = docker_sdk.from_env()
            containers = client.containers.list(all=True)
            docker_info = "\n".join([
                f"  {c.name}: {c.status}" for c in containers
            ])
            docker_section = f"\n\n=== Docker Containers ===\n{docker_info}"
        except Exception:
            docker_section = ""
        return f"=== Disk Usage ===\n{df.stdout.strip()}\n\n=== Key Directories ===\n{du.stdout.strip()}{docker_section}"
    except subprocess.TimeoutExpired:
        return "Disk usage check timed out"
    except Exception as e:
        logger.error(f"disk_usage error: {e}")
        return f"Error: {e}"

if __name__ == "__main__":
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount
    from starlette.requests import Request as StarletteRequest

    sse = SseServerTransport("/messages/")

    def _auth_ok(request) -> bool:
        if not MCP_AUTH_TOKEN:
            return True
        return request.headers.get("Authorization", "") == f"Bearer {MCP_AUTH_TOKEN}"

    async def health(request):
        try:
            devices = get_devices()
            online = sum(1 for d in devices if d.get("connectedToControl"))
            return JSONResponse({
                "status": "ok",
                "devices": len(devices),
                "online": online,
                "offline": len(devices) - online,
            })
        except Exception as e:
            logger.error(f"health check failed: {e}")
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)

    async def handle_sse(request):
        if not _auth_ok(request):
            logger.warning(f"Unauthorized SSE from {request.client.host}")
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp._mcp_server.run(streams[0], streams[1], mcp._mcp_server.create_initialization_options())

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

    logger.info("MCP Tailscale Monitor starting on :8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)
