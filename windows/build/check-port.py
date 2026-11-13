#!/usr/bin/env python3
"""Static checks of the *patched* BunkerWeb sources (no Windows, no build): did the port leave valid files behind?

  python windows/build/check-port.py <patched-src> [<upstream-src>]

For every file the port changed or added (compared with the pristine upstream tree, default bunkerweb/src):
  * .py     compiles under the running Python
  * .lua    parses with LuaJIT (through lupa; set BW_REQUIRE_LUPA=1 to make a missing lupa an error)
  * .conf   parses as a Jinja2 template, so a regex-removed block cannot leave an unbalanced {% if %}
  * .json   is valid JSON
plus a few invariants of the port (wincompat is required first in init_by_lua, nothing re-enables SO_REUSEPORT, the
marker file exists). Reports every problem, exit code 1 if there is any.
"""

import filecmp
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECKED = {".py", ".lua", ".conf", ".json"}


def changed_files(src, upstream):
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix not in CHECKED:
            continue
        rel = path.relative_to(src)
        if "__pycache__" in rel.parts:
            continue
        other = upstream / rel
        if not other.is_file() or not filecmp.cmp(path, other, shallow=False):
            yield rel, path


def lua_loader():
    try:
        from lupa.luajit21 import LuaRuntime
    except ImportError:
        try:
            from lupa import LuaRuntime
        except ImportError:
            if os.environ.get("BW_REQUIRE_LUPA"):
                raise SystemExit("lupa is not installed (pip install lupa)")
            print("WARNING: lupa is not installed, Lua files are not parsed")
            return None
    lua = LuaRuntime(unpack_returned_tuples=True)
    return lua.eval("function(src, name) local f, err = loadstring(src, name) return err end")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = Path(sys.argv[1]).resolve()
    upstream = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else REPO / "bunkerweb" / "src"
    problems = []
    counts = {suffix: 0 for suffix in CHECKED}

    try:
        from jinja2 import Environment

        jinja = Environment(lstrip_blocks=True, trim_blocks=True, keep_trailing_newline=True)
    except ImportError:
        if os.environ.get("BW_REQUIRE_LUPA"):
            raise SystemExit("jinja2 is not installed (pip install jinja2)")
        print("WARNING: jinja2 is not installed, templates are not parsed")
        jinja = None
    lua_check = lua_loader()

    for rel, path in changed_files(src, upstream):
        counts[path.suffix] += 1
        text = path.read_text(encoding="utf-8", newline="")
        name = rel.as_posix()
        try:
            if path.suffix == ".py":
                compile(text, name, "exec")
            elif path.suffix == ".lua" and lua_check:
                error = lua_check(text, "@" + name)
                if error:
                    problems.append(f"{name}: Lua syntax: {error}")
            elif path.suffix == ".conf" and jinja and ("{%" in text or "{{" in text):
                jinja.parse(text)
            elif path.suffix == ".json":
                json.loads(text)
        except SyntaxError as exc:
            problems.append(f"{name}: Python syntax: {exc.msg} (line {exc.lineno})")
        except Exception as exc:  # noqa: BLE001 - jinja2.TemplateSyntaxError, json.JSONDecodeError
            problems.append(f"{name}: {type(exc).__name__}: {exc}")

    # invariants of the port
    if not (src / "WINDOWS_PORT").is_file():
        problems.append("WINDOWS_PORT marker is missing (apply-windows-port.py did not run to the end)")
    for rel in ("common/confs/init-lua.conf", "common/confs/init-stream-lua.conf"):
        conf = src / rel
        if not conf.is_file() or 'init_by_lua_block {\n\trequire "bunkerweb.wincompat"\n' not in conf.read_text(encoding="utf-8", newline=""):
            problems.append(f"{rel}: wincompat is not the first statement of init_by_lua_block")
    for conf in sorted((src / "common").rglob("*.conf")):
        text = conf.read_text(encoding="utf-8", newline="")
        if " reuseport" in text:
            problems.append(f"{conf.relative_to(src).as_posix()}: reuseport is still present (nginx for Windows has no SO_REUSEPORT)")

    summary = ", ".join(f"{n} {suffix}" for suffix, n in sorted(counts.items()))
    if problems:
        print(f"Port check FAILED ({summary} checked):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"Port check OK ({summary} changed/added files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
