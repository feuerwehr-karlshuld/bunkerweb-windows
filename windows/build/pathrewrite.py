"""Step 13 of the Windows port: move BunkerWeb's own Linux locations below /bunkerweb.

Everything of this port lives below C:\\bunkerweb, in the same sub-layout as the Linux packages
(C:\\bunkerweb\\etc\\bunkerweb, C:\\bunkerweb\\var\\lib\\bunkerweb, ...). BunkerWeb hardcodes the Linux locations, so they
are rewritten: "/etc/bunkerweb" becomes "/bunkerweb/etc/bunkerweb". A rooted path without drive letter resolves against
the drive of the working directory (C:, every service runs in C:\\bunkerweb).

Only BunkerWeb's own locations are rewritten. System paths (/dev/null, /usr/bin/env, /etc/os-release, ...) and the OWASP
CRS data files are left alone. The rewrite is idempotent (the inserted prefix makes the inner "/etc/bunkerweb" fail the
lookbehind). Files copied from windows/overlay already use the final paths.

This module is import-safe (no side effects): apply-windows-port.py drives it, test-port.py has golden tests for it.
"""

import re
from pathlib import Path

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

# the rewrite must have reached the places the services depend on (a miss means upstream moved something)
REQUIRED_REWRITTEN = (
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
)

_REWRITTEN_RE = re.compile(re.escape(ROOT) + "/" + OWN_DIRS)
_SKIPPED_RE = re.compile("/" + OWN_DIRS)


def rewrite_text(text, suffix=".py"):
    """Return (new_text, number_of_rewritten_occurrences)."""
    result, n_text = TEXT_PATH_RE.subn(ROOT, text)
    n_parts = 0
    if suffix == ".py":
        result, n_parts = PART_PATH_RE.subn(r'\1"bunkerweb", ', result)
    return result, n_text + n_parts


def skipped_locations(text):
    """Linux locations that are still present after the rewrite (built from a variable, ...): only reported."""
    text = _REWRITTEN_RE.sub("", text)
    return [text[max(0, m.start() - 12):m.end() + 12] for m in _SKIPPED_RE.finditer(text)]


def candidates(src):
    """Yield (relative path, absolute path) of every file the rewrite looks at."""
    src = Path(src)
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if rel.as_posix() in REWRITE_EXTRA:
            yield rel, path
            continue
        if rel.parts[0] in REWRITE_SKIP_TOP or REWRITE_SKIP_ANY.intersection(rel.parts):
            continue
        # shell scripts: only the plugin helpers that jobs may run through busybox
        if path.suffix in REWRITE_SUFFIXES or (path.suffix == ".sh" and rel.parts[0] == "common" and "core" in rel.parts):
            yield rel, path


def rewrite_tree(src, skip=frozenset()):
    """Rewrite the tree in place (files in `skip`, e.g. overlay files, are left alone). Returns {relpath: count}."""
    rewritten = {}
    for rel, path in candidates(src):
        if path.resolve() in skip:
            continue
        try:
            text = path.read_text(encoding="utf-8", newline="")
        except UnicodeDecodeError:
            continue
        result, count = rewrite_text(text, path.suffix)
        if count:
            path.write_text(result, encoding="utf-8", newline="")
            rewritten[rel.as_posix()] = count
    return rewritten


def report_skipped(src, skip=frozenset()):
    """Yield (relpath, excerpt) for Linux locations the rewrite deliberately did not move."""
    for rel, path in candidates(src):
        if path.resolve() in skip:
            continue
        try:
            text = path.read_text(encoding="utf-8", newline="")
        except UnicodeDecodeError:
            continue
        for excerpt in skipped_locations(text):
            yield rel.as_posix(), excerpt
