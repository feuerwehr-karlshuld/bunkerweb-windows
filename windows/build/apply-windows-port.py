#!/usr/bin/env python3
"""Apply the Windows port to a *copy* of bunkerweb/src.

The upstream submodule is never modified. The port is made of, in this order:

  1. overlay files (windows/overlay/src), copied into the tree; a file that already exists upstream is an error,
  2. the patch series windows/patches (git format-patch files, see windows/patches/README.md): multi-line hunks with
     context, one concern per patch, each with its reason and upstream status,
  3. anchored, counted one-line replacements below (rep/rep_re): if upstream changes and an anchor no longer matches,
     the build fails loudly instead of producing a silently broken package,
  4. the path rewrite of windows/build/pathrewrite.py (BunkerWeb's /etc/bunkerweb, ... -> /bunkerweb/etc/bunkerweb, ...).

Everything that can be done at runtime is NOT done here: the Lua side (ffi.C lookups, io.open modes, resty.signal,
ngx.process.get_master_pid, /proc/uptime) is handled by bunkerweb/wincompat.lua + winffi.lua, the Python side (isabs of
rooted paths, POSIX functions, subprocess) by python-site/sitecustomize.py. Fewer source edits = fewer anchors that
upstream can break.

Every failure is collected and listed together at the end.

Usage: python apply-windows-port.py <copy-of-bunkerweb-src> <windows-overlay-src>
"""

import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pathrewrite  # noqa: E402
import patchseries  # noqa: E402

SRC = Path(sys.argv[1]).resolve()
OVERLAY = Path(sys.argv[2]).resolve()
PATCHES = Path(__file__).resolve().parents[1] / "patches"

# Overlay files that deliberately replace a file of the same name in bunkerweb/src (none today). Anything else that
# collides with an upstream file means upstream started to ship that file: the overlay must not silently overwrite it.
OVERLAY_OVERRIDES = frozenset()

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


print(f"Applying Windows port to {SRC}")

# ---------------------------------------------------------------- 1. overlay files
overlay_files = set()
for item in sorted(OVERLAY.rglob("*")):
    if item.is_file():
        target = SRC / item.relative_to(OVERLAY)
        rel_posix = item.relative_to(OVERLAY).as_posix()
        if target.exists() and rel_posix not in OVERLAY_OVERRIDES:
            errors.append(f"overlay {rel_posix}: upstream now ships this file (list it in OVERLAY_OVERRIDES if replacing it is intended)")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        overlay_files.add(target.resolve())
        print(f"  overlay {target.relative_to(SRC)}")

# ---------------------------------------------------------------- 1b. patch series
# Applied to the pristine upstream files, before any replacement below (they never touch the same hunks).
errors.extend(patchseries.apply_series(SRC, PATCHES))

# ---------------------------------------------------------------- 2. nginx.conf template
# All modules are linked statically into nginx.exe, there is no load_module.
rep_re(
    "common/confs/nginx.conf",
    r'\{% if os\.path\.isfile\("/usr/lib64/nginx/modules/ngx_stream_module\.so"\) \+%\}.*?ngx_http_lua_upstream_module\.so;\n',
    "# modules are linked statically on Windows\n",
    count=1,
    flags=re.DOTALL,
)
# os.uname() does not exist on Windows (a sitecustomize shim would also change what platform.uname() reports)
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

# ---------------------------------------------------------------- 3b. DNS_RESOLVERS default
# Upstream defaults to 127.0.0.11, the embedded DNS server of Docker. On Windows nothing listens there: every DNS lookup of
# nginx/Lua (DNSBL, hostname backends, ...) fails with "WSARecv() failed (10054)" and slows init_worker down. Same public
# resolvers as the Linux packages and the variables.env template (bwsvc.py).
rep(
    "common/settings.json",
    '"default": "127.0.0.11",\n    "help": "DNS addresses of resolvers to use.",',
    '"default": "9.9.9.9 149.112.112.112 8.8.8.8 8.8.4.4",\n    "help": "DNS addresses of resolvers to use.",',
    1,
)

# nginx resolves -e (the early error log) with its own code that only knows drive-letter paths: "/bunkerweb/..." would be
# appended to the prefix. (error_log in nginx.conf is fine, nginx handles rooted paths there.)
rep("bw/lua/bunkerweb/api.lua", "-t -e /var/log/bunkerweb/error.log", "-t -e C:/bunkerweb/var/log/bunkerweb/error.log", 1)

# ---------------------------------------------------------------- 4. load the Windows shim first
# wincompat.lua then installs everything else at runtime (see its header): ffi proxy, io.open binary writes,
# resty.signal / ngx.process.get_master_pid replacements, /proc/uptime.
for rel in ("common/confs/init-lua.conf", "common/confs/init-stream-lua.conf"):
    rep(rel, "init_by_lua_block {\n", 'init_by_lua_block {\n\trequire "bunkerweb.wincompat"\n', 1)

# ---------------------------------------------------------------- 5. letsencrypt: PYTHONPATH separator
LEU = "common/core/letsencrypt/jobs/letsencrypt_utils.py"
rep(LEU, 'current_pythonpath.split(":")', 'current_pythonpath.split(__import__("os").pathsep)', 1)
rep(LEU, '":".join(pythonpath_entries)', '__import__("os").pathsep.join(pythonpath_entries)', 1)

# ---------------------------------------------------------------- 6. letsencrypt: hooks need an interpreter
CBN = "common/core/letsencrypt/jobs/certbot-new.py"
for hook in ("certbot-auth.py", "certbot-cleanup.py"):
    rep(
        CBN,
        f'JOBS_PATH.joinpath("{hook}").as_posix(),',
        f'__import__("sys").executable.replace("\\\\", "/") + " " + JOBS_PATH.joinpath("{hook}").as_posix(),',
        1,
    )

# ---------------------------------------------------------------- 7. single root folder: C:\bunkerweb
# See windows/build/pathrewrite.py. Runs last, so everything above anchors on the original Linux paths.
rewritten = pathrewrite.rewrite_tree(SRC, skip=overlay_files)
print(f"  rewrote BunkerWeb paths below {pathrewrite.ROOT} in {len(rewritten)} files ({sum(rewritten.values())} occurrences)")
for rel in pathrewrite.REQUIRED_REWRITTEN:
    if rel not in rewritten:
        errors.append(f"{rel}: expected BunkerWeb paths to be rewritten below {pathrewrite.ROOT}, found none")
# Linux locations the rewrite deliberately skipped (e.g. built from a variable): only reported, to be reviewed
for rel, excerpt in pathrewrite.report_skipped(SRC, skip=overlay_files):
    print(f"  NOTE {rel}: Linux path not moved below {pathrewrite.ROOT}: ...{excerpt!r}")

# ---------------------------------------------------------------- 8. Windows marker
(SRC / "WINDOWS_PORT").write_text("applied\n", encoding="utf-8")

if errors:
    print(
        "\nWindows port FAILED - upstream changed. Adapt windows/build/apply-windows-port.py (one-line replacements) or the "
        "patch series (python windows/build/patchtool.py import --rev <upstream tag>; see windows/patches/README.md):",
        file=sys.stderr,
    )
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

print("Windows port applied successfully")
