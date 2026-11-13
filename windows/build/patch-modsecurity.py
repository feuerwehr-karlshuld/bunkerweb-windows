#!/usr/bin/env python3
"""Patch the vendored ModSecurity (deps/src/modsecurity) in a *copy* of bunkerweb/src.

This is deliberately separate from apply-windows-port.py: the `modsecurity` CI job (MSVC + conan, ~20 minutes) only depends
on this script, so edits to the rest of the port do not invalidate its build cache. Everything that changes what
build-modsecurity.ps1 compiles belongs here, nothing else does.

Like apply-windows-port.py every edit is an anchored, counted replacement that fails the build when upstream changed.

Usage: python patch-modsecurity.py <copy-of-bunkerweb-src>
"""

import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1]).resolve()

errors = []


def rep_re(rel, pattern, new, count=1, flags=0):
    path = SRC / rel
    if not path.is_file():
        errors.append(f"{rel}: file not found")
        return 0
    text = path.read_text(encoding="utf-8", newline="")
    result, found = re.subn(pattern, new, text, flags=flags)
    if found != count:
        errors.append(f"{rel}: expected {count} x /{pattern[:70]}/, found {found}")
        return 0
    path.write_text(result, encoding="utf-8", newline="")
    print(f"  patched {rel} (regex, {found}x)")
    return found


print(f"Patching ModSecurity in {SRC}")

# ---------------------------------------------------------------- ModSecurity win32 CMake: library only
rep_re(
    "deps/src/modsecurity/build/win32/CMakeLists.txt",
    r"# tests\n#\n\nproject\(libModSecurityTests\).*\Z",
    "# tests, examples and tools are not built for the BunkerWeb Windows package\n",
    count=1,
    flags=re.DOTALL,
)

if errors:
    print("\nERROR: the ModSecurity patch does not apply to this upstream version:")
    for error in errors:
        print(f"  - {error}")
    sys.exit(1)
print("ModSecurity patch applied.")
