"""BunkerWeb for Windows: control helper (bwctl).

    bwctl status             service states, nginx health and the internal API
    bwctl reload             ask nginx (internal API, API_HTTP_PORT) to reload its current configuration
    bwctl reload-scheduler   ask the scheduler to re-read variables.env and regenerate the config
    bwctl restart [svc ...]  restart services (default: all installed ones, in dependency order)
    bwctl firewall           (re)create the Windows Firewall rule for HTTP_PORT/HTTPS_PORT from variables.env
    bwctl test               run `nginx -t` against the live configuration
    bwctl logs [name] [-n N] print the tail of a log file (error, access, scheduler, ui, api, service-<name>)
"""

import ctypes
import json
import subprocess
import sys
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT_DIR = r"C:\bunkerweb"
ETC = Path(ROOT_DIR) / "etc" / "bunkerweb"
LOG_DIR = Path(ROOT_DIR) / "var" / "log" / "bunkerweb"
NGINX_EXE = ROOT_DIR + r"\usr\sbin\nginx.exe"
SERVICES = ("bunkerweb-nginx", "bunkerweb-scheduler", "bunkerweb-ui", "bunkerweb-api")
SCHEDULER_RELOAD_EVENT = "Global\\bunkerweb_scheduler_reload"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


def parse_env_file(path: Path) -> dict:
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


def variables() -> dict:
    return parse_env_file(ETC / "variables.env")


def service_state(name: str) -> str:
    proc = subprocess.run(["sc.exe", "query", name], capture_output=True, text=True)
    if proc.returncode != 0:
        return "not installed"
    for line in proc.stdout.splitlines():
        if "STATE" in line:
            return line.split()[-1].lower()
    return "unknown"


def http(url: str, method: str = "GET", host: str = None, token: str = None, timeout: int = 10):
    headers = {}
    if host:
        headers["Host"] = host
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, method=method, headers=headers, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # connection refused, timeout ...
        return 0, str(e)


def port_setting(v: dict, key: str, default: str) -> str:
    value = v.get(key, "").strip() or default
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise ValueError(f"{key}={value!r} in variables.env is not a valid TCP port")
    return value


def cmd_status(_args) -> int:
    v = variables()
    for name in SERVICES:
        print(f"{name:<22} {service_state(name)}")
    health_port = v.get("HEALTHCHECK_PORT", "").strip() or "6000"
    code, body = http(f"http://127.0.0.1:{health_port}/healthz", host="healthcheck.bunkerweb.io", timeout=3)
    print(f"{'nginx health':<22} {body.strip() if code == 200 else f'unreachable ({body.strip()[:80]})'}")
    api_ip = v.get("API_LISTEN_IP", "127.0.0.1").strip() or "127.0.0.1"
    api_port = v.get("API_HTTP_PORT", "5000").strip() or "5000"
    code, body = http(f"http://{api_ip}:{api_port}/ping", host=v.get("API_SERVER_NAME", "bwapi").strip() or "bwapi", token=v.get("API_TOKEN", "").strip(), timeout=3)
    print(f"{'internal api':<22} {'ok' if code == 200 else f'{code} {body.strip()[:80]}'}")
    if "IS_LOADING" in v and v["IS_LOADING"].strip().lower() == "yes":
        print("\nNOTE: IS_LOADING=yes is set in C:\\bunkerweb\\etc\\bunkerweb\\variables.env - BunkerWeb serves the loading page until you remove it.")
    return 0


def cmd_reload(_args) -> int:
    v = variables()
    api_ip = v.get("API_LISTEN_IP", "127.0.0.1").strip() or "127.0.0.1"
    api_port = v.get("API_HTTP_PORT", "5000").strip() or "5000"
    code, body = http(
        f"http://{api_ip}:{api_port}/reload?test=yes",
        method="POST",
        host=v.get("API_SERVER_NAME", "bwapi").strip() or "bwapi",
        token=v.get("API_TOKEN", "").strip(),
        timeout=60,
    )
    print(body.strip())
    try:
        return 0 if code == 200 and json.loads(body).get("status") == "success" else 1
    except ValueError:
        return 1


def cmd_reload_scheduler(_args) -> int:
    handle = kernel32.OpenEventW(0x0002, False, SCHEDULER_RELOAD_EVENT)  # EVENT_MODIFY_STATE
    if not handle:
        print(f"The scheduler is not running (error {ctypes.get_last_error()}).", file=sys.stderr)
        return 1
    try:
        ok = kernel32.SetEvent(handle)
    finally:
        kernel32.CloseHandle(handle)
    print("Reload requested - the scheduler regenerates and applies the configuration (see: bwctl logs scheduler).")
    return 0 if ok else 1


def cmd_restart(args) -> int:
    targets = [s if s.startswith("bunkerweb-") else f"bunkerweb-{s}" for s in args] or [
        s for s in SERVICES if service_state(s) not in ("not installed",) and (s != "bunkerweb-api" or service_state(s) == "running")
    ]
    script = "; ".join(f"Restart-Service -Force -Name '{s}'" for s in targets)
    return subprocess.run(["powershell.exe", "-NoProfile", "-Command", "$ErrorActionPreference='Stop'; " + script]).returncode


def cmd_firewall(_args) -> int:
    v = variables()
    try:
        ports = sorted({port_setting(v, "HTTP_PORT", "80"), port_setting(v, "HTTPS_PORT", "443")}, key=int)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    script = (
        "$ErrorActionPreference='Stop'; "
        "Remove-NetFirewallRule -Group 'BunkerWeb' -ErrorAction SilentlyContinue; "
        "New-NetFirewallRule -Group 'BunkerWeb' -DisplayName 'BunkerWeb HTTP/HTTPS' -Direction Inbound -Protocol TCP "
        f"-LocalPort {','.join(ports)} -Action Allow -Profile Any | Out-Null"
    )
    code = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]).returncode
    if code == 0:
        print(f"Windows Firewall: inbound TCP {', '.join(ports)} allowed (rule group 'BunkerWeb').")
    return code


def cmd_test(_args) -> int:
    return subprocess.run([NGINX_EXE, "-t", "-e", "C:/bunkerweb/var/log/bunkerweb/error.log"], cwd=ROOT_DIR).returncode


def cmd_logs(args) -> int:
    lines = 50
    if "-n" in args:
        i = args.index("-n")
        lines = int(args[i + 1])
        args = args[:i] + args[i + 2 :]
    name = args[0] if args else "error"
    if name.startswith("service-"):
        component = name.split("-", 1)[1]
        candidates = sorted((LOG_DIR / "service").glob(f"bunkerweb-{component}*.log"), key=lambda p: p.stat().st_mtime)
        path = candidates[-1] if candidates else None
    else:
        path = LOG_DIR / f"{name}.log"
    if not path or not path.is_file():
        print(f"No such log: {name}", file=sys.stderr)
        return 1
    with path.open("rb") as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(max(0, size - 256 * 1024))
        content = file.read().decode("utf-8", "replace").splitlines()
    print(f"==> {path} <==")
    print("\n".join(content[-lines:]))
    return 0


COMMANDS = {
    "status": cmd_status,
    "reload": cmd_reload,
    "reload-scheduler": cmd_reload_scheduler,
    "restart": cmd_restart,
    "firewall": cmd_firewall,
    "test": cmd_test,
    "logs": cmd_logs,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]))
