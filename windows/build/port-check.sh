#!/usr/bin/env bash
# Fast checks of the Windows port on any platform with bash, git and Python 3.14 (no Windows, no build tools).
# Used by .github/workflows/port-check.yml; run it locally the same way:
#
#   pip install lupa jinja2 && BW_REQUIRE_LUPA=1 bash windows/build/port-check.sh
#
# It copies bunkerweb/src to build/src (the submodule is only read), applies the port exactly like the real pipeline
# does (.github/actions/prepare-src) and then checks the result. All checks run; the exit code is non-zero if any failed.
set -uo pipefail
cd "$(dirname "$0")/../.."

failed=()
step() {
  local name="$1"
  shift
  echo
  echo "=== ${name}"
  if ! "$@"; then
    failed+=("${name}")
  fi
}

step "patch series lint" python windows/build/patchtool.py lint
step "unit tests (path rewrite, isabs shim, ffi proxy)" python windows/build/test-port.py

rm -rf build/src
mkdir -p build
cp -a bunkerweb/src build/src

step "patch-modsecurity.py" python windows/build/patch-modsecurity.py build/src
if python windows/build/apply-windows-port.py build/src windows/overlay/src; then
  step "static checks of the patched sources" python windows/build/check-port.py build/src bunkerweb/src
  step "hazard scan against windows/build/hazards.baseline.txt" python windows/build/scan-hazards.py build/src
else
  failed+=("apply-windows-port.py")
fi

echo
if [ ${#failed[@]} -gt 0 ]; then
  echo "PORT CHECK FAILED: ${failed[*]}" >&2
  exit 1
fi
echo "Port check passed for BunkerWeb $(tr -d '[:space:]' < bunkerweb/src/VERSION)"
