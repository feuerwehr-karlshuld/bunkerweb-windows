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

# ---------------------------------------------------------------- 3c. no HTTP/3 (QUIC) on Windows
# nginx for Windows has no http_v3_module: the `http3` directive, `listen ... quic` and the $http3 variable do not exist and
# nginx refuses the whole configuration ("unknown directive http3"). HTTP3 defaults to yes upstream, so the blocks are
# switched off for good (whatever HTTP3 says) and the connection limiter gets an always-empty key for the HTTP/3 zone.
rep("common/confs/default-server-http.conf", 'HTTP3 == "yes"', "false", 1)
rep("common/confs/server-http/ssl-certificate-lua.conf", 'HTTP3 == "yes"', "false", 1)
LIMITCONN = "common/core/limit/confs/http/limitconn.conf"
rep(LIMITCONN, 'map "$http2$http3" $v1ip {', 'map "$http2" $v1ip {', 1)
rep(
    LIMITCONN,
    'map $http3 $v3ip {\n\t# This will be "h3" for HTTP/3 connections. Empty otherwise.\n\tdefault $binary_remote_addr;\n\t""      "";\n}',
    'map $http2 $v3ip {\n\t# no HTTP/3 on Windows: always empty, so this limit never applies\n\tdefault "";\n}',
    1,
)
rep("common/core/misc/plugin.json", '"default": "yes",\n      "help": "Support HTTP3 protocol', '"default": "no",\n      "help": "Not available on Windows (nginx has no HTTP/3 there). Support HTTP3 protocol', 1)

# nginx resolves -e (the early error log) with its own code that only knows drive-letter paths: "/bunkerweb/..." would be
# appended to the prefix. (error_log in nginx.conf is fine, nginx handles rooted paths there.)
rep("bw/lua/bunkerweb/api.lua", "-t -e /var/log/bunkerweb/error.log", "-t -e C:/bunkerweb/var/log/bunkerweb/error.log", 1)

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

# The pushed .tar.gz is written with io.open(tmp, "w+"): on Windows that is TEXT mode, every 0x0A byte of the (binary) gzip
# stream becomes 0x0D 0x0A and `tar xzf` then fails ("cannot extract archive", HTTP 500 for /confs, /cache, /custom_configs,
# /pro_plugins; tiny archives without a 0x0A byte happened to work). Binary mode is what Linux always was.
rep(API, 'local file, err = open(tmp, "w+")', 'local file, err = open(tmp, "w+b")', 1)

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

# ---------------------------------------------------------------- 7b. third-party Lua libraries that call libc/winsock through ffi.C
# On Windows ffi.C only searches the executable's exports, lua51.dll, the C runtime, kernel32, user32 and gdi32. It does not
# search ws2_32.dll (inet_pton, inet_ntop, ntohl) and the UCRT has no setenv/unsetenv. These libraries are copied from the
# patched tree by build-native.sh. Every one of these calls is lazy: the module loads fine and then fails on the first request.
# - lua-resty-ipmatcher: every IP/network check of BunkerWeb (whitelist, blacklist, limit, utils.is_ipv4, ...)
# - lua-resty-env: env.set("is_whitelisted", ...) runs in the set phase of every request (whitelist plugin)
# - lua-resty-openssl altname: IP subject alternative names of certificates
# - lua-resty-core shdict: C.free() of values larger than the stack buffer (BunkerWeb keeps big tables in shared dicts)
WS2_C = (
    "local C = ffi.C\n"
    'if ffi.os == "Windows" then\n'
    "    -- Windows port: ffi.C does not search ws2_32.dll\n"
    "    C = setmetatable({}, { __index = ffi.load(\"ws2_32\") })\n"
    "end\n"
)
rep_re("deps/src/lua-resty-ipmatcher/resty/ipmatcher.lua", r"local C\s+= ffi\.C\n", WS2_C.replace("\\", "\\\\"), 1)
rep("deps/src/lua-resty-openssl/lib/resty/openssl/x509/altname.lua", "local C = ffi.C\n", WS2_C, 1)
rep(
    "deps/src/lua-resty-env/src/resty/env.lua",
    "local C = ffi.C\n",
    "local C = ffi.C\n"
    'if jit.os == "Windows" then\n'
    "    -- Windows port: the C runtime has no setenv/unsetenv, _putenv_s changes the environment the CRT (and libModSecurity) see\n"
    '    ffi.cdef("int _putenv_s(const char *name, const char *value);")\n'
    '    ffi.cdef("char ***__p__environ(void);")\n'
    '    local ucrt = ffi.load("ucrtbase")\n'
    "    local own = {\n"
    "        setenv = function(name, value) return ucrt._putenv_s(name, value) == 0 and 0 or -1 end,\n"
    '        unsetenv = function(name) return ucrt._putenv_s(name, "") == 0 and 0 or -1 end,\n'
    "    }\n"
    "    -- 'environ' is not an exported symbol of the UCRT, the pointer is fetched on every access (putenv may reallocate it)\n"
    "    C = setmetatable({}, { __index = function(_, k)\n"
    "        if own[k] then return own[k] end\n"
    "        if k == 'environ' then return ucrt.__p__environ()[0] end\n"
    "        return ffi.C[k]\n"
    "    end })\n"
    "end\n",
    1,
)
SHDICT = "deps/src/lua-resty-core/lib/resty/core/shdict.lua"
insert_after(
    SHDICT,
    'local value_type = ffi_new("int[1]")\n',
    "local bw_free\n"
    'if jit.os == "Windows" then\n'
    "    -- Windows port: free() has to come from the C runtime nginx.exe allocated the buffer with\n"
    '    local ok, ucrt = pcall(ffi.load, "ucrtbase")\n'
    "    if ok then\n"
    "        local ok2, f = pcall(function() return ucrt.free end)\n"
    "        if ok2 then bw_free = f end\n"
    "    end\n"
    "end\n"
    "bw_free = bw_free or C.free\n",
)
rep(SHDICT, "C.free(buf)", "bw_free(buf)", 2)

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

# ---------------------------------------------------------------- 11. ModSecurity win32 CMake: moved to patch-modsecurity.py
# (the modsecurity CI job caches its build on that script only, not on this one; the prepare-src action runs both)

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

# ---------------------------------------------------------------- 12c. config generator: no fork() on Windows
# Templator.render() forces the "fork" multiprocessing start method (so children inherit globals) and renders the
# servers in a ProcessPoolExecutor. Windows only has "spawn", which would have to pickle the Templator and re-import
# the generator in every child. The rendering is a few Jinja templates per server: do it in one worker thread.
TPL = "common/gen/Templator.py"
rep(TPL, "from concurrent.futures import ProcessPoolExecutor, as_completed", "from concurrent.futures import ThreadPoolExecutor, as_completed", 1)
rep(TPL, "        _ensure_fork_start_method()\n        _ssl_ecdh_curve_resolution_logged = False", "        _ssl_ecdh_curve_resolution_logged = False", 1)
rep(TPL, "with ProcessPoolExecutor(max_workers=max_workers) as executor:", "with ThreadPoolExecutor(max_workers=1) as executor:", 1)

# ---------------------------------------------------------------- 12d. rooted paths are not absolute (Python >= 3.13)
# Since Python 3.13 a path without drive letter ("/bunkerweb/var/cache/bunkerweb", see step 13) is NOT absolute on
# Windows: Path.is_absolute() and os.path.isabs() are False. BunkerWeb's validators use them to reject relative paths
# (and to block absolute members/links of archives), so a rooted path counts as absolute here.
rep(
    "common/utils/cache_restore.py",
    "if not path.is_absolute() or path == path.parent",
    "if not (path.is_absolute() or path.root) or path == path.parent",
    1,
)
rep(
    "common/utils/common_utils.py",
    "if Path(member.linkname).is_absolute():",
    "if Path(member.linkname).is_absolute() or Path(member.linkname).root:",
    1,
)
rep("ui/app/routes/logs.py", "isabs(current_file) or", 'isabs(current_file) or current_file.startswith(("/", "\\\\")) or', 2)
rep("ui/app/routes/plugins.py", "isabs(file) or", 'isabs(file) or file.startswith(("/", "\\\\")) or', 1)
rep("ui/app/routes/plugins.py", "isabs(member.name) or", 'isabs(member.name) or member.name.startswith(("/", "\\\\")) or', 1)

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
