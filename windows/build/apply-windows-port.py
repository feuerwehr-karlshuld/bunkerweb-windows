#!/usr/bin/env python3
"""Apply the Windows port to a *copy* of bunkerweb/src.

The upstream submodule is never modified. Every edit is an anchored, counted
replacement: if upstream changes and an anchor no longer matches, the build
fails loudly instead of producing a silently broken package.

Usage: python apply-windows-port.py <copy-of-bunkerweb-src> <windows-overlay-src>
"""

import re
import shutil
import sys
from pathlib import Path

SRC = Path(sys.argv[1]).resolve()
OVERLAY = Path(sys.argv[2]).resolve()

errors = []


def _read(rel):
    path = SRC / rel
    if not path.is_file():
        errors.append(f"{rel}: file not found")
        return None, path
    return path.read_text(encoding="utf-8", newline=""), path


def rep(rel, old, new, count=1):
    """Replace exactly `count` occurrences of `old` (count=None: at least one)."""
    text, path = _read(rel)
    if text is None:
        return 0
    found = text.count(old)
    if (count is None and found == 0) or (count is not None and found != count):
        errors.append(f"{rel}: expected {count if count is not None else '>=1'} x {old[:70]!r}, found {found}")
        return 0
    path.write_text(text.replace(old, new), encoding="utf-8", newline="")
    print(f"  patched {rel} ({found}x)")
    return found


def rep_re(rel, pattern, new, count=1, flags=0):
    text, path = _read(rel)
    if text is None:
        return 0
    result, found = re.subn(pattern, new, text, flags=flags)
    if found != count:
        errors.append(f"{rel}: expected {count} x /{pattern[:70]}/, found {found}")
        return 0
    path.write_text(result, encoding="utf-8", newline="")
    print(f"  patched {rel} (regex, {found}x)")
    return found


def insert_after(rel, anchor, addition):
    return rep(rel, anchor, anchor + addition, 1)


print(f"Applying Windows port to {SRC}")

# ---------------------------------------------------------------- 1. overlay files
overlay_files = set()
for item in OVERLAY.rglob("*"):
    if item.is_file():
        target = SRC / item.relative_to(OVERLAY)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        overlay_files.add(target.resolve())
        print(f"  overlay {target.relative_to(SRC)}")

# ---------------------------------------------------------------- 2. nginx.conf template
# All modules are linked statically into nginx.exe, there is no load_module.
rep_re(
    "common/confs/nginx.conf",
    r'\{% if os\.path\.isfile\("/usr/lib64/nginx/modules/ngx_stream_module\.so"\) \+%\}.*?ngx_http_lua_upstream_module\.so;\n',
    "# modules are linked statically on Windows\n",
    count=1,
    flags=re.DOTALL,
)
# os.uname() does not exist on Windows
rep("common/confs/nginx.conf", 'os.uname().sysname == "FreeBSD"', "false", 2)
# nginx for Windows only has select()
rep("common/confs/nginx.conf", "use epoll;", "use select;", 1)

# ---------------------------------------------------------------- 3. no SO_REUSEPORT on Windows
total = 0
for rel in (
    "common/confs/api.conf",
    "common/confs/default-server-http.conf",
    "common/confs/server-http/ssl-certificate-lua.conf",
    "common/confs/server-stream/server-stream.conf",
):
    total += rep(rel, " reuseport", "", None)
if total == 0:
    errors.append("no reuseport occurrence removed")

# ---------------------------------------------------------------- 3b. configurable health check port
# Upstream hardcodes 127.0.0.1:6000 for the health check server (healthcheck.conf is a global Jinja template).
# Make it a regular global setting so that every port of the port can be chosen in variables.env
# (HEALTHCHECK_PORT, default 6000 = unchanged behaviour). bwsvc.py and bwctl.py read the same value.
rep("common/confs/healthcheck.conf", "listen 127.0.0.1:6000;", "listen 127.0.0.1:{{ HEALTHCHECK_PORT }};", 1)
insert_after(
    "common/settings.json",
    '    "id": "api-https-listen",\n    "label": "API HTTPS port number",\n    "regex": "^\\\\d+$",\n    "type": "number"\n  },\n',
    '  "HEALTHCHECK_PORT": {\n'
    '    "context": "global",\n'
    '    "default": "6000",\n'
    '    "help": "Listen port number (127.0.0.1 only) of the health check server (/healthz).",\n'
    '    "id": "healthcheck-port",\n'
    '    "label": "Health check port number",\n'
    '    "regex": "^\\\\d+$",\n'
    '    "type": "number"\n'
    "  },\n",
)

# ---------------------------------------------------------------- 4. load the Windows shim first
for rel in ("common/confs/init-lua.conf", "common/confs/init-stream-lua.conf"):
    rep(rel, "init_by_lua_block {\n", 'init_by_lua_block {\n\trequire "bunkerweb.wincompat"\n', 1)

# ---------------------------------------------------------------- 5. bunkerweb/api.lua
API = "bw/lua/bunkerweb/api.lua"
rep(API, 'local rsignal = require "resty.signal"', 'local rsignal = jit.os ~= "Windows" and require("resty.signal") or nil', 1)
rep(
    API,
    "local kill = rsignal.kill\nlocal get_master_pid = process.get_master_pid",
    'local wincompat = require "bunkerweb.wincompat"\n'
    "local kill = rsignal and rsignal.kill or wincompat.kill\n"
    "local get_master_pid = rsignal and process.get_master_pid or wincompat.get_master_pid",
    1,
)
# ffi.C.* (fopen/fileno/flock/fputs/fclose/fchmod) -> Win32 emulation on Windows
rep(API, "ffi.C.", "C_.", 18)
insert_after(
    API,
    "int fchmod(int fd, unsigned int mode);\n]]\n",
    'local C_ = jit.os == "Windows" and require("bunkerweb.wincompat").libc or ffi.C\n',
)
rep(
    API,
    '\tlocal file = open("/proc/uptime", "r")',
    '\tif jit.os == "Windows" then return require("bunkerweb.wincompat").uptime() end\n\tlocal file = open("/proc/uptime", "r")',
    1,
)

# ---------------------------------------------------------------- 6. crowdsec stream lock
LOCK = "common/core/crowdsec/lib/stream_lock.lua"
rep(LOCK, "ffi.C.", "C_.", 6)
insert_after(
    LOCK,
    "int flock(int fd, int operation);\n]]\n",
    'local C_ = jit.os == "Windows" and require("bunkerweb.wincompat").libc or ffi.C\n',
)

# ---------------------------------------------------------------- 7. crowdsec inet_pton
rep(
    "common/core/crowdsec/lib/iputils.lua",
    "local res = ffi.C.inet_pton(LINUX_AF_INET6, ip, tmp_addr)",
    'local res; if jit.os == "Windows" then res = ffi.load("ws2_32").inet_pton(23, ip, tmp_addr) '
    "else res = ffi.C.inet_pton(LINUX_AF_INET6, ip, tmp_addr) end",
    1,
)

# ---------------------------------------------------------------- 8. gen/sync_tree.py: /proc/uptime
SYNC = "common/gen/sync_tree.py"
# replace first: the helper inserted below contains the original text itself
rep(SYNC, 'Path("/proc/uptime").read_bytes()', "_bw_uptime_bytes()", 1)
insert_after(
    SYNC,
    "from typing import Callable, Iterator, Optional\n",
    "\n\ndef _bw_uptime_bytes() -> bytes:\n"
    "    import sys\n"
    '    if sys.platform == "win32":\n'
    "        import ctypes\n"
    "        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong\n"
    "        ticks = ctypes.windll.kernel32.GetTickCount64()\n"
    '        return f"{ticks / 1000:.2f} 0\\n".encode()\n'
    '    return Path("/proc/uptime").read_bytes()\n',
)

# ---------------------------------------------------------------- 9. letsencrypt: PYTHONPATH separator
LEU = "common/core/letsencrypt/jobs/letsencrypt_utils.py"
rep(LEU, 'current_pythonpath.split(":")', 'current_pythonpath.split(__import__("os").pathsep)', 1)
rep(LEU, '":".join(pythonpath_entries)', '__import__("os").pathsep.join(pythonpath_entries)', 1)

# ---------------------------------------------------------------- 10. letsencrypt: hooks need an interpreter
CBN = "common/core/letsencrypt/jobs/certbot-new.py"
for hook in ("certbot-auth.py", "certbot-cleanup.py"):
    rep(
        CBN,
        f'JOBS_PATH.joinpath("{hook}").as_posix(),',
        f'__import__("sys").executable.replace("\\\\", "/") + " " + JOBS_PATH.joinpath("{hook}").as_posix(),',
        1,
    )

# ---------------------------------------------------------------- 11. ModSecurity win32 CMake: library only
rep_re(
    "deps/src/modsecurity/build/win32/CMakeLists.txt",
    r"# tests\n#\n\nproject\(libModSecurityTests\).*\Z",
    "# tests, examples and tools are not built for the BunkerWeb Windows package\n",
    count=1,
    flags=re.DOTALL,
)

# ---------------------------------------------------------------- 12. nginx configure: system libs on win32
# nginx's configure skips the detection of system OpenSSL/PCRE2/zlib on win32
# (it expects source trees). With MSYS2 the system libraries work fine.
NGX = "deps/src/nginx/auto/lib"
rep(f"{NGX}/openssl/conf", '    if [ "$NGX_PLATFORM" != win32 ]; then\n\n        OPENSSL=NO', "    if true; then\n\n        OPENSSL=NO", 1)
rep(f"{NGX}/pcre/conf", '    if [ "$NGX_PLATFORM" != win32 ]; then\n        PCRE=NO\n    fi', "    PCRE=NO", 1)
rep(f"{NGX}/zlib/conf", '    if [ "$NGX_PLATFORM" != win32 ]; then\n        ZLIB=NO', "    if true; then\n        ZLIB=NO", 1)

# ---------------------------------------------------------------- 12b. modsecurity-nginx: dd() debug macro
# nginx for Windows does not define NGX_HAVE_VARIADIC_MACROS, so the non-debug dd() becomes a real
# function whose arguments are still compiled - and one of them (clcf) only exists in debug builds.
# GCC has variadic macros, always use the empty macro.
rep(
    "deps/src/modsecurity-nginx/src/ddebug.h",
    "#else\n\n#   if (NGX_HAVE_VARIADIC_MACROS)\n\n#       define dd(...)\n",
    "#else\n\n#   if 1 /* Windows port: NGX_HAVE_VARIADIC_MACROS is not defined on win32 */\n\n#       define dd(...)\n",
    1,
)

# ---------------------------------------------------------------- 13. single root folder: C:\bunkerweb
# Everything of this port lives below C:\bunkerweb, in the same sub-layout as the Linux packages
# (C:\bunkerweb\etc\bunkerweb, C:\bunkerweb\var\lib\bunkerweb, ...). BunkerWeb hardcodes the Linux locations,
# so they are rewritten here: "/etc/bunkerweb" becomes "/bunkerweb/etc/bunkerweb". A rooted path without
# drive letter resolves against the drive of the working directory (C:, every service runs in C:\bunkerweb),
# the same mechanism that resolved "/etc/bunkerweb" to C:\etc\bunkerweb before.
#
# Only BunkerWeb's own locations are rewritten. System paths (/dev/null, /usr/bin/env, /etc/os-release, ...) and
# the OWASP CRS data files are left alone. The rewrite is idempotent (the inserted prefix makes the inner
# "/etc/bunkerweb" fail the lookbehind). Files copied from windows/overlay already use the final paths.
ROOT = "/bunkerweb"
OWN_DIRS = r"(?:etc/bunkerweb|etc/nginx|usr/share/bunkerweb|var/(?:cache|tmp|lib|log|run)/bunkerweb|tmp/bunkerweb|var/www/html)"
# "/etc/bunkerweb", "sqlite:////var/lib/bunkerweb/...", "{% if x %}/var/www/html"; not after a word character or
# the end of a template expression ("{{ x }}/etc/..." is relative to x)
TEXT_PATH_RE = re.compile(r"(?<![\w.~$)\]-])(?<!\}\})(?=/" + OWN_DIRS + ")")
# join(sep, "usr", "share", "bunkerweb"), Path("/", "etc", "bunkerweb"), os.path.join(os.sep, ...)
_Q = r"[\"']"
_PARTS = (
    rf"(?:{_Q}usr{_Q}\s*,\s*{_Q}share{_Q}\s*,\s*{_Q}bunkerweb{_Q}"
    rf"|{_Q}etc{_Q}\s*,\s*{_Q}(?:bunkerweb|nginx){_Q}"
    rf"|{_Q}var{_Q}\s*,\s*{_Q}(?:cache|tmp|lib|log|run){_Q}\s*,\s*{_Q}bunkerweb{_Q}"
    rf"|{_Q}tmp{_Q}\s*,\s*{_Q}bunkerweb{_Q}"
    rf"|{_Q}var{_Q}\s*,\s*{_Q}www{_Q}\s*,\s*{_Q}html{_Q})"
)
PART_PATH_RE = re.compile(rf"((?:\bsep|{_Q}/{_Q})\s*,\s*)(?={_PARTS})")

REWRITE_SUFFIXES = {".py", ".lua", ".conf", ".json", ".modsec"}
REWRITE_SKIP_TOP = {"deps", "linux", "all-in-one", "autoconf"}  # third party sources, Linux packaging, containers
REWRITE_SKIP_ANY = {"static", "locales", "node_modules", "__pycache__", "coreruleset-v3", "coreruleset-v4"}
# third party Lua libraries that BunkerWeb patches to load its libz/libmaxminddb by absolute path
REWRITE_EXTRA = ("deps/src/lua-ffi-zlib/lib/ffi-zlib.lua", "deps/src/luajit-geoip/geoip/mmdb.lua")


def rewrite_candidates():
    for path in sorted(SRC.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(SRC)
        if rel.as_posix() in REWRITE_EXTRA:
            yield rel, path
            continue
        if rel.parts[0] in REWRITE_SKIP_TOP or REWRITE_SKIP_ANY.intersection(rel.parts):
            continue
        # shell scripts: only the plugin helpers that jobs may run through busybox
        if path.suffix in REWRITE_SUFFIXES or (path.suffix == ".sh" and rel.parts[0] == "common" and "core" in rel.parts):
            yield rel, path


rewritten = {}
for rel, path in rewrite_candidates():
    if path.resolve() in overlay_files:
        continue
    try:
        text = path.read_text(encoding="utf-8", newline="")
    except UnicodeDecodeError:
        continue
    result, n_text = TEXT_PATH_RE.subn(ROOT, text)
    n_parts = 0
    if path.suffix == ".py":
        result, n_parts = PART_PATH_RE.subn(r'\1"bunkerweb", ', result)
    if n_text or n_parts:
        path.write_text(result, encoding="utf-8", newline="")
        rewritten[rel.as_posix()] = n_text + n_parts
print(f"  rewrote BunkerWeb paths below {ROOT} in {len(rewritten)} files ({sum(rewritten.values())} occurrences)")
# the rewrite must have reached the places the services depend on (a miss means upstream moved something)
for rel in (
    *REWRITE_EXTRA,
    "common/confs/nginx.conf",
    "common/confs/init-lua.conf",
    "common/settings.json",
    "common/core/db/plugin.json",
    "common/gen/main.py",
    "common/utils/logger.py",
    "common/core/letsencrypt/jobs/letsencrypt_utils.py",
    "common/core/letsencrypt/letsencrypt.lua",
    "scheduler/main.py",
    "scheduler/JobScheduler.py",
    "bw/lua/bunkerweb/utils.lua",
    "bw/lua/bunkerweb/api.lua",
):
    if rel not in rewritten:
        errors.append(f"{rel}: expected BunkerWeb paths to be rewritten below {ROOT}, found none")
# Linux locations the rewrite deliberately skipped (e.g. built from a variable): only reported, to be reviewed
REWRITTEN_RE = re.compile(re.escape(ROOT) + "/" + OWN_DIRS)
SKIPPED_RE = re.compile("/" + OWN_DIRS)
for rel, path in rewrite_candidates():
    if path.resolve() in overlay_files:
        continue
    try:
        text = path.read_text(encoding="utf-8", newline="")
    except UnicodeDecodeError:
        continue
    text = REWRITTEN_RE.sub("", text)
    for m in SKIPPED_RE.finditer(text):
        print(f"  NOTE {rel.as_posix()}: Linux path not moved below {ROOT}: ...{text[max(0, m.start() - 12):m.end() + 12]!r}")

# ---------------------------------------------------------------- 14. Windows marker
(SRC / "WINDOWS_PORT").write_text("applied\n", encoding="utf-8")

if errors:
    print("\nWindows port FAILED - upstream changed, adjust windows/build/apply-windows-port.py:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

print("Windows port applied successfully")
