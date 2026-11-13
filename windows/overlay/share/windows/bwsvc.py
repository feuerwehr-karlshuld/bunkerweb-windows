"""BunkerWeb for Windows: service runner.

Each Windows service (installed with WinSW) runs:

    C:\\bunkerweb\\usr\\share\\bunkerweb\\python\\python.exe C:\\bunkerweb\\usr\\share\\bunkerweb\\windows\\bwsvc.py <component>

with the working directory C:\\bunkerweb. Everything lives below that single root folder. The build rewrites
every POSIX style absolute path of BunkerWeb ("/etc/bunkerweb/...") to "/bunkerweb/etc/bunkerweb/...", which
Windows resolves against the root of the current drive (C:), i.e. to C:\\bunkerweb\\etc\\bunkerweb\\...

Components (ports of the Linux scripts in src/linux/scripts/):
    nginx      -> start.sh               (temporary config, nginx master, health wait)
    scheduler  -> bunkerweb-scheduler.sh (DB migration, scheduler main loop in-process)
    ui         -> bunkerweb-ui.sh        (Flask app served by waitress instead of gunicorn)
    api        -> bunkerweb-api.sh       (FastAPI app served by uvicorn instead of gunicorn)
"""

import ctypes
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

BW = Path("/bunkerweb/usr/share/bunkerweb")
DEPS = BW / "deps" / "python"
ETC = Path("/bunkerweb/etc/bunkerweb")
NGINX_ETC = Path("/bunkerweb/etc/nginx")
RUN_DIR = Path("/bunkerweb/var/run/bunkerweb")
TMP_DIR = Path("/bunkerweb/var/tmp/bunkerweb")
LOG_DIR = Path("/bunkerweb/var/log/bunkerweb")
LIB_DIR = Path("/bunkerweb/var/lib/bunkerweb")
CACHE_DIR = Path("/bunkerweb/var/cache/bunkerweb")
ROOT_DIR = "C:\\bunkerweb"
NGINX_EXE = ROOT_DIR + "\\usr\\sbin\\nginx.exe"
PYTHON = sys.executable

SCHEDULER_RELOAD_EVENT = "Global\\bunkerweb_scheduler_reload"

VARIABLES_ENV_DEFAULT = """# BunkerWeb for Windows - global configuration (configuration as code)
# remove IS_LOADING=yes when your config is ready
IS_LOADING=yes
SERVER_NAME=
DNS_RESOLVERS=9.9.9.9 149.112.112.112 8.8.8.8 8.8.4.4
HTTP_PORT=80
HTTPS_PORT=443
API_LISTEN_IP=127.0.0.1
API_TOKEN=
KEEP_CONFIG_ON_RESTART=no
MULTISITE=yes
# nginx for Windows effectively uses a single worker process
WORKER_PROCESSES=1
UI_HOST=http://127.0.0.1:7000
"""

SCHEDULER_ENV_DEFAULT = """LOG_LEVEL=info
LOG_TYPES=file
# LOG_FILE_PATH=/bunkerweb/var/log/bunkerweb/scheduler.log
# in seconds
HEALTHCHECK_INTERVAL=30

# in seconds (the minimum is calculated by the formula and whichever is greater: RELOAD_MIN_TIMEOUT or count(SERVERS) * 2))
RELOAD_MIN_TIMEOUT=5
"""

UI_ENV_DEFAULT = """# OVERRIDE_ADMIN_CREDS=no
ADMIN_USERNAME=
ADMIN_PASSWORD=
# FLASK_SECRET=changeme
# TOTP_ENCRYPTION_KEYS=changeme
LISTEN_ADDR=127.0.0.1
# LISTEN_PORT=7000
# MAX_CONTENT_LENGTH=50MB
FORWARDED_ALLOW_IPS=127.0.0.1,::1
PROXY_ALLOW_IPS=127.0.0.1,::1
# ENABLE_HEALTHCHECK=no
# UI_USE_REDIS=yes
LOG_LEVEL=info
LOG_TYPES=file
# LOG_FILE_PATH=/bunkerweb/var/log/bunkerweb/ui.log
"""

API_ENV_DEFAULT = """# API_USERNAME=
# API_PASSWORD=
# API_TOKEN=
LISTEN_ADDR=127.0.0.1
LISTEN_PORT=8888
WHITELIST_IPS=127.0.0.1
LOG_LEVEL=info
LOG_TYPES=file
# LOG_FILE_PATH=/bunkerweb/var/log/bunkerweb/api.log
"""

TMP_ENV_DEFAULTS = {
    "DNS_RESOLVERS": "9.9.9.9 149.112.112.112 8.8.8.8 8.8.4.4",
    "API_LISTEN_HTTP": "yes",
    "API_LISTEN_HTTPS": "no",
    "API_HTTP_PORT": "5000",
    "API_HTTPS_PORT": "5443",
    "API_LISTEN_IP": "127.0.0.1",
    "API_SERVER_NAME": "bwapi",
    "API_WHITELIST_IP": "127.0.0.0/8",
    "API_TOKEN": "",
    "USE_REAL_IP": "no",
    "USE_PROXY_PROTOCOL": "no",
    "REAL_IP_FROM": "192.168.0.0/16 172.16.0.0/12 10.0.0.0/8",
    "REAL_IP_HEADER": "X-Forwarded-For",
    "HTTP_PORT": "80",
    "HTTPS_PORT": "443",
    "KEEP_CONFIG_ON_RESTART": "no",
}

# ---------------------------------------------------------------------------------------- helpers

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
kernel32.GetCurrentProcess.restype = wintypes.HANDLE

EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000


def log(component: str, message: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] [{component.upper()}] {message}", flush=True)


def ensure_dirs() -> None:
    for path in (
        RUN_DIR,
        TMP_DIR,
        LOG_DIR,
        LIB_DIR,
        CACHE_DIR,
        ETC,
        ETC / "plugins",
        ETC / "pro" / "plugins",
        Path("/bunkerweb/tmp/bunkerweb/ui-workers"),
        Path("/bunkerweb/tmp/bunkerweb/api-workers"),
        Path("/bunkerweb/var/www/html"),
    ):
        path.mkdir(parents=True, exist_ok=True)
    for sub in (
        "http",
        "stream",
        "server-http",
        "server-stream",
        "default-server-http",
        "modsec",
        "modsec-crs",
        "crs-plugins-before",
        "crs-plugins-after",
    ):
        (ETC / "configs" / sub).mkdir(parents=True, exist_ok=True)


def write_if_missing(path: Path, content: str, component: str) -> None:
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        log(component, f"Created default {path.as_posix()}")


def parse_env_file(path: Path, identifiers_only: bool = False) -> dict:
    """KEY=VALUE lines like the Linux export_env_file helper; tolerant to CRLF and a UTF-8 BOM."""
    values = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip("\r")
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if identifiers_only and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        values[key] = value
    return values


def base_env(*extra_paths: Path) -> dict:
    env = dict(os.environ)
    paths = [str(DEPS), *(str(p) for p in extra_paths)]
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def apply_env(env: dict) -> None:
    os.environ.clear()
    os.environ.update(env)
    for path in reversed(env.get("PYTHONPATH", "").split(os.pathsep)):
        if path and path not in sys.path:
            sys.path.insert(1, path)


def kill_children_with_me() -> None:
    """Put this process (and thus every child it starts) into a kill-on-close job object."""

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("r", "w", "o", "rb", "wb", "ob")]

    class BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # JobObjectExtendedLimitInformation
    kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())
    globals()["_JOB_HANDLE"] = job  # keep it open for the lifetime of the process


def set_named_event(name: str) -> bool:
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def nginx_signal(pid: int, sig: str) -> bool:
    return set_named_event(f"Global\\ngx_{sig}_{pid}")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # sitecustomize turns this into a liveness probe
        return True
    except OSError:
        return False


def read_pid(path: Path):
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


STOP = threading.Event()


def install_stop_handlers(component: str) -> None:
    def handler(signum, frame):
        log(component, f"Stop requested (signal {signum})")
        STOP.set()

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
        signal.signal(sig, handler)


def run_python(component: str, args: list, env: dict, cwd=None) -> int:
    log(component, "Running " + " ".join(str(a) for a in args))
    return subprocess.run([PYTHON, *[str(a) for a in args]], env=env, cwd=cwd, stdin=subprocess.DEVNULL).returncode


# ---------------------------------------------------------------------------------------- nginx


def stop_nginx(component: str, pid: int, proc=None, quit_timeout: int = 20) -> None:
    if not pid or not pid_alive(pid):
        return
    log(component, f"Stopping nginx (PID {pid}) gracefully ...")
    nginx_signal(pid, "quit")
    deadline = time.monotonic() + quit_timeout
    while time.monotonic() < deadline and pid_alive(pid):
        time.sleep(0.5)
    if pid_alive(pid):
        log(component, "Graceful stop timed out, sending stop ...")
        nginx_signal(pid, "stop")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(0.5)
    if pid_alive(pid) and proc is not None:
        log(component, "nginx still running, terminating it")
        proc.kill()


def healthz() -> str:
    request = urllib.request.Request("http://127.0.0.1:6000/healthz", headers={"Host": "healthcheck.bunkerweb.io"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.read().decode("utf-8", "replace").strip()
    except Exception:
        return ""


def component_nginx() -> int:
    c = "nginx"
    kill_children_with_me()
    install_stop_handlers(c)
    ensure_dirs()
    write_if_missing(ETC / "variables.env", VARIABLES_ENV_DEFAULT, c)

    pid_file = RUN_DIR / "nginx.pid"
    old_pid = read_pid(pid_file)
    if old_pid:
        stop_nginx(c, old_pid)
    pid_file.unlink(missing_ok=True)

    variables = parse_env_file(ETC / "variables.env")
    values = dict(TMP_ENV_DEFAULTS)
    for key in TMP_ENV_DEFAULTS:
        if variables.get(key, "").strip():
            values[key] = variables[key].strip()

    tmp_env = TMP_DIR / "tmp.env"
    sentinel = NGINX_ETC / ".bw-install-incomplete"
    needs_config = Path("/bunkerweb/var/tmp/bunkerweb_needs_config")
    regenerate = values["KEEP_CONFIG_ON_RESTART"] == "no" or not tmp_env.is_file()
    if not regenerate:
        if sentinel.exists():
            log(c, "The previous configuration install was interrupted: regenerating the temporary config")
            regenerate = True
        else:
            log(c, "Preserving current config on restart and asking the scheduler for the latest one ...")
            needs_config.touch()

    lines = ["IS_LOADING=yes", "USE_BUNKERNET=no", "SEND_ANONYMOUS_REPORT=no", "SERVER_NAME="]
    lines += [f"{key}={values[key]}" for key in TMP_ENV_DEFAULTS]
    tmp_env.write_text("\n".join(lines) + "\n", encoding="utf-8")

    (TMP_DIR / "reloading").unlink(missing_ok=True)
    Path("/bunkerweb/var/tmp/bunkerweb_reloading").unlink(missing_ok=True)

    env = base_env()
    if regenerate:
        needs_config.unlink(missing_ok=True)
        if run_python(c, [BW / "gen" / "main.py", "--variables", tmp_env.as_posix()], env, cwd=ROOT_DIR) != 0:
            log(c, f"Error while generating config from {tmp_env.as_posix()}")
            return 1
        sentinel.unlink(missing_ok=True)

    for name in ("client_temp", "proxy_temp", "fastcgi_temp", "uwsgi_temp", "scgi_temp"):
        target = TMP_DIR / name
        if target.is_dir():
            for child in target.iterdir():
                try:
                    if child.is_dir():
                        __import__("shutil").rmtree(child, ignore_errors=True)
                    else:
                        child.unlink()
                except OSError:
                    pass

    if run_python(c, [BW / "utils" / "modsecurity_audit.py", "/bunkerweb/etc/nginx/variables.env"], env, cwd=ROOT_DIR) != 0:
        log(c, "Invalid ModSecurity audit storage, refusing to start nginx")
        return 1

    log(c, "Starting nginx ...")
    proc = subprocess.Popen(
        [NGINX_EXE, "-e", "/bunkerweb/var/log/bunkerweb/error.log"],
        cwd=ROOT_DIR,
        env=env,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    for _ in range(30):
        if STOP.is_set() or proc.poll() is not None:
            break
        state = healthz()
        if state in ("ok", "loading"):
            log(c, f"nginx started (PID {proc.pid}, health: {state})")
            break
        log(c, "Waiting for nginx to start ...")
        time.sleep(1)
    else:
        log(c, "nginx did not answer on the health endpoint within 30 seconds")
        stop_nginx(c, proc.pid, proc)
        return 1

    while not STOP.is_set():
        code = proc.poll()
        if code is not None:
            log(c, f"nginx exited with code {code}")
            pid_file.unlink(missing_ok=True)
            return code or 1
        time.sleep(1)

    stop_nginx(c, proc.pid, proc)
    pid_file.unlink(missing_ok=True)
    log(c, "nginx is stopped")
    return 0


# ---------------------------------------------------------------------------------------- scheduler


def scheduler_env() -> dict:
    env = base_env(BW / "db", BW / "utils")
    for path in (ETC / "variables.env", ETC / "scheduler.env"):
        env.update(parse_env_file(path, identifiers_only=True))
    env.setdefault("LOG_LEVEL", "INFO")
    env.setdefault("CUSTOM_LOG_LEVEL", env["LOG_LEVEL"])
    env["LOG_TYPES"] = env.get("SCHEDULER_LOG_TYPES") or env.get("LOG_TYPES") or "file"
    env["LOG_FILE_PATH"] = env.get("SCHEDULER_LOG_FILE_PATH") or env.get("LOG_FILE_PATH") or "/bunkerweb/var/log/bunkerweb/scheduler.log"
    env["LOG_SYSLOG_TAG"] = env.get("SCHEDULER_LOG_SYSLOG_TAG") or env.get("LOG_SYSLOG_TAG") or "bw-scheduler"
    for key, default in (
        ("LOG_SYSLOG_ADDRESS", ""),
        ("HEALTHCHECK_INTERVAL", "30"),
        ("RELOAD_MIN_TIMEOUT", "5"),
        ("DISABLE_CONFIGURATION_TESTING", "no"),
        ("IGNORE_FAIL_SENDING_CONFIG", "no"),
        ("IGNORE_REGEX_CHECK", "no"),
        ("DATABASE_URI", "sqlite:////bunkerweb/var/lib/bunkerweb/db.sqlite3"),
        ("DATABASE_RETRY_TIMEOUT", "60"),
        ("DATABASE_LOG_LEVEL", "WARNING"),
    ):
        if not env.get(key):
            env[key] = default
    return env


DB_VERSION_SCRIPT = r"""
import sqlalchemy as sa
from traceback import format_exc
from Database import Database
from logger import getLogger

LOGGER = getLogger("SCHEDULER.WINDOWS")
db = None
try:
    db = Database(LOGGER)
    with db.sql_engine.connect() as conn:
        inspector = sa.inspect(db.sql_engine)
        if "bw_metadata" in inspector.get_table_names():
            result = conn.execute(sa.text("SELECT version FROM bw_metadata WHERE id = 1"))
            print("BWVERSION=" + str(next(result)[0]))
        else:
            print("BWVERSION=" + INSTALLED)
except BaseException:
    with open("/bunkerweb/var/tmp/bunkerweb/database_error", "w", encoding="utf-8") as file:
        file.write(format_exc())
    print("BWVERSION=none")
if db:
    print("BWURI=" + db.database_uri)
"""

DB_UPDATE_SCRIPT = r"""
import sqlalchemy as sa
from Database import Database
from logger import getLogger

db = Database(getLogger("SCHEDULER.WINDOWS"))
with db.sql_engine.connect() as conn:
    conn.execute(sa.text("UPDATE bw_metadata SET version = :v WHERE id = 1"), {"v": INSTALLED})
    conn.commit()
"""


def migrate_database(c: str, env: dict) -> bool:
    installed = (BW / "VERSION").read_text(encoding="utf-8").strip()
    error_file = TMP_DIR / "database_error"
    error_file.unlink(missing_ok=True)
    proc = subprocess.run(
        [PYTHON, "-c", f"INSTALLED = {installed!r}\n" + DB_VERSION_SCRIPT],
        env=env,
        cwd=ROOT_DIR,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
    )
    output = proc.stdout or ""
    current = re.search(r"^BWVERSION=(.*)$", output, re.M)
    uri = re.search(r"^BWURI=(.*)$", output, re.M)
    if proc.returncode != 0 or not current:
        log(c, f"Failed to retrieve database version:\n{output}")
        return False
    if error_file.is_file():
        log(c, "Failed to retrieve database version: " + error_file.read_text(encoding="utf-8", errors="replace"))
        error_file.unlink(missing_ok=True)
        return False
    if not uri:
        log(c, "Failed to retrieve database URI")
        return False
    current = current.group(1).strip()
    env["DATABASE_URI"] = uri.group(1).strip()
    if current == installed:
        return True

    alembic_dir = BW / "db" / "alembic"
    if current in ("dev", "testing"):
        code = subprocess.run([PYTHON, "-c", f"INSTALLED = {installed!r}\n" + DB_UPDATE_SCRIPT], env=env, cwd=ROOT_DIR).returncode
        return code == 0

    database = env["DATABASE_URI"].split(":", 1)[0].split("+", 1)[0]
    ini = alembic_dir / "alembic.ini"
    ini.write_text(
        re.sub(r"(?m)^version_locations =.*$", f"version_locations = {database}_versions", ini.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    normalized = re.sub(r"[.\-~]", "_", current)
    revision = None
    for migration in (alembic_dir / f"{database}_versions").glob(f"*_upgrade_to_version_{normalized}.py"):
        revision = migration.name.split("_", 1)[0]
    if not revision:
        log(c, f"No migration file found for database version: {current}")
        return False
    if subprocess.run([PYTHON, "-m", "alembic", "stamp", revision], env=env, cwd=alembic_dir).returncode != 0:
        log(c, f"Failed to stamp database with revision: {revision}, migration aborted")
        return True
    log(c, "Running database migration ...")
    if subprocess.run([PYTHON, "-m", "alembic", "upgrade", "head"], env=env, cwd=alembic_dir).returncode != 0:
        log(c, "Database migration failed")
        return False
    log(c, "Database migration completed successfully")
    return True


def component_scheduler() -> int:
    c = "scheduler"
    kill_children_with_me()
    ensure_dirs()
    write_if_missing(ETC / "scheduler.env", SCHEDULER_ENV_DEFAULT, c)
    write_if_missing(ETC / "variables.env", VARIABLES_ENV_DEFAULT, c)

    pid_file = RUN_DIR / "scheduler.pid"
    pid = read_pid(pid_file)
    if pid is None or pid == os.getpid() or not pid_alive(pid):
        pid_file.unlink(missing_ok=True)

    env = scheduler_env()
    log(c, "Checking database version ...")
    if not migrate_database(c, env):
        return 1

    # Reload requests (bwctl reload-scheduler) arrive as a named event and are turned into the
    # SIGHUP handler of the scheduler (signal.SIGHUP is an alias of SIGBREAK, see sitecustomize).
    event = kernel32.CreateEventW(None, False, False, SCHEDULER_RELOAD_EVENT)

    def reload_listener():
        import _thread

        while event:
            if kernel32.WaitForSingleObject(event, 0xFFFFFFFF) == 0:
                log(c, "Reload requested, forwarding to the scheduler (SIGHUP)")
                _thread.interrupt_main(signal.SIGHUP)

    if event:
        threading.Thread(target=reload_listener, name="bw-reload-listener", daemon=True).start()
    else:
        log(c, f"Could not create the reload event (error {ctypes.get_last_error()}), bwctl reload-scheduler unavailable")

    apply_env(env)
    os.chdir(ROOT_DIR)
    scheduler_dir = BW / "scheduler"
    sys.path.insert(0, str(scheduler_dir))
    sys.argv = [str(scheduler_dir / "main.py"), "--variables", "/bunkerweb/etc/bunkerweb/variables.env"]
    log(c, "Executing scheduler ...")
    import runpy

    runpy.run_path(str(scheduler_dir / "main.py"), run_name="__main__")
    return 0


# ---------------------------------------------------------------------------------------- ui


def ui_env() -> dict:
    env = base_env(BW / "ui", BW / "utils", BW / "api", BW / "db")
    ui_values = parse_env_file(ETC / "ui.env")
    variables = parse_env_file(ETC / "variables.env")

    def get(name, default):
        return ui_values.get(name) or variables.get(name) or default

    env["LISTEN_ADDR"] = get("UI_LISTEN_ADDR", "") or get("LISTEN_ADDR", "127.0.0.1")
    env["LISTEN_PORT"] = get("UI_LISTEN_PORT", "") or get("LISTEN_PORT", "7000")
    env["FORWARDED_ALLOW_IPS"] = get("UI_FORWARDED_ALLOW_IPS", "") or get("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")
    env["PROXY_ALLOW_IPS"] = get("UI_PROXY_ALLOW_IPS", "") or get("PROXY_ALLOW_IPS", env["FORWARDED_ALLOW_IPS"])
    env["LOG_TYPES"] = get("UI_LOG_TYPES", "") or get("LOG_TYPES", "file")
    env["LOG_FILE_PATH"] = get("UI_LOG_FILE_PATH", "") or get("LOG_FILE_PATH", "/bunkerweb/var/log/bunkerweb/ui.log")
    env["LOG_SYSLOG_TAG"] = get("UI_LOG_SYSLOG_TAG", "") or get("LOG_SYSLOG_TAG", "bw-ui")
    env["CAPTURE_OUTPUT"] = "yes"
    env.update(parse_env_file(ETC / "variables.env", identifiers_only=True))
    env.update(parse_env_file(ETC / "ui.env", identifiers_only=True))
    return env


def supervise(c: str, child_component: str, env: dict) -> int:
    """Runs `bwsvc.py <child_component>` and restarts it when it exits with code 3 (restart request)."""
    kill_children_with_me()
    install_stop_handlers(c)
    while not STOP.is_set():
        proc = subprocess.Popen([PYTHON, __file__, child_component], env=env, cwd=ROOT_DIR, stdin=subprocess.DEVNULL)
        while proc.poll() is None and not STOP.is_set():
            time.sleep(0.5)
        if STOP.is_set():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return 0
        if proc.returncode == 3:
            log(c, "Restart requested, starting a fresh process ...")
            continue
        log(c, f"{child_component} exited with code {proc.returncode}")
        return proc.returncode or 1
    return 0


def first_ip(value: str) -> str:
    entries = [entry.strip() for entry in value.replace(" ", ",").split(",") if entry.strip()]
    if not entries or "*" in entries:
        return "*"
    return entries[0].split("/")[0]


def component_ui() -> int:
    c = "ui"
    ensure_dirs()
    write_if_missing(ETC / "ui.env", UI_ENV_DEFAULT, c)
    for name in ("tmp-ui.pid", "ui.pid"):
        (RUN_DIR / name).unlink(missing_ok=True)
    (TMP_DIR / "ui.error").unlink(missing_ok=True)
    return supervise(c, "ui-child", ui_env())


def component_ui_child() -> int:
    import importlib.util

    ui_dir = BW / "ui"
    os.chdir(ui_dir)
    sys.path.insert(0, str(ui_dir))
    for path in (DEPS, BW / "utils", BW / "api", BW / "db"):
        if str(path) not in sys.path:
            sys.path.append(str(path))

    # gunicorn process management -> plain process exits handled by the supervisor
    import app.utils as ui_utils

    def stop(status, _stop=True):
        os._exit(status)

    def restart_workers():
        time.sleep(3)
        os._exit(3)

    ui_utils.stop = stop
    ui_utils.restart_workers = restart_workers

    spec = importlib.util.spec_from_file_location("bw_ui_gunicorn_conf", ui_dir / "utils" / "gunicorn.conf.py")
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)
    conf.on_starting(None)
    (RUN_DIR / "ui.pid").write_text(str(os.getpid()), encoding="utf-8")

    import main  # noqa: E402 - the Flask application of the UI

    conf.when_ready(None)
    from waitress import serve

    host, port = conf.LISTEN_ADDR, int(conf.LISTEN_PORT)
    log("ui", f"Serving the web UI on http://{host}:{port}")
    try:
        serve(
            main.app,
            host=host,
            port=port,
            threads=max(4, int(getattr(conf, "threads", 4))),
            trusted_proxy=first_ip(conf.FORWARDED_ALLOW_IPS),
            trusted_proxy_count=1,
            trusted_proxy_headers="x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port",
            clear_untrusted_proxy_headers=True,
            max_request_body_size=1 << 30,
            ident="bunkerweb-ui",
        )
    finally:
        conf.on_exit(None)
    return 0


# ---------------------------------------------------------------------------------------- api


def api_env() -> dict:
    env = base_env(BW / "api", BW / "utils", BW / "db")
    env.update(parse_env_file(ETC / "variables.env", identifiers_only=True))
    env.update(parse_env_file(ETC / "api.env", identifiers_only=True))
    env.setdefault("LOG_TYPES", "file")
    env.setdefault("LOG_FILE_PATH", "/bunkerweb/var/log/bunkerweb/api.log")
    env["CAPTURE_OUTPUT"] = "yes"
    return env


def component_api() -> int:
    c = "api"
    ensure_dirs()
    write_if_missing(ETC / "api.env", API_ENV_DEFAULT, c)
    (RUN_DIR / "api.pid").unlink(missing_ok=True)
    return supervise(c, "api-child", api_env())


def component_api_child() -> int:
    import importlib.util

    api_dir = BW / "api"
    os.chdir(api_dir)
    sys.path.insert(0, str(api_dir))
    for path in (DEPS, BW / "utils", BW / "db"):
        if str(path) not in sys.path:
            sys.path.append(str(path))

    spec = importlib.util.spec_from_file_location("bw_api_gunicorn_conf", api_dir / "utils" / "gunicorn.conf.py")
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)
    conf.on_starting(None)
    (RUN_DIR / "api.pid").write_text(str(os.getpid()), encoding="utf-8")
    conf.when_ready(None)

    import uvicorn

    host, port = conf.LISTEN_ADDR, int(conf.LISTEN_PORT)
    log("api", f"Serving the API on http://{host}:{port}")
    try:
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            loop="asyncio",
            proxy_headers=True,
            forwarded_allow_ips=conf.FORWARDED_ALLOW_IPS,
            server_header=False,
            date_header=False,
            log_level=str(getattr(conf, "loglevel", "info")).lower(),
        )
    finally:
        conf.on_exit(None)
    return 0


# ---------------------------------------------------------------------------------------- main

COMPONENTS = {
    "nginx": component_nginx,
    "scheduler": component_scheduler,
    "ui": component_ui,
    "ui-child": component_ui_child,
    "api": component_api,
    "api-child": component_api_child,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMPONENTS:
        print(f"usage: bwsvc.py <{'|'.join(COMPONENTS)}>", file=sys.stderr)
        sys.exit(2)
    os.chdir(ROOT_DIR)
    sys.exit(COMPONENTS[sys.argv[1]]())
