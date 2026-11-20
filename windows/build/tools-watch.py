#!/usr/bin/env python3
"""Look for newer versions of the tools and GitHub Actions that this repository pins itself (not BunkerWeb's own
dependencies: those move with the submodule, see port-check.yml's upstream-watch).

What is checked, and where the pin is read from (so there is no second list to maintain):

  * every `uses: owner/repo[/path]@ref` of .github/workflows/*.yml and .github/actions/*/action.yml,
    compared with the repository's latest release: a new *major* is reported (`@v7` follows v7.x on its own),
    a pin to an exact version (`@v7.0.1`) is reported when anything newer exists;
  * WinSW         `WINSW_URL`          in .github/workflows/bunkerweb-windows.yml
  * busybox-w32   `BUSYBOX_URL`        in the same file (latest: frippery.org, fallback: the GitHub tags)
  * waitress      `$WAITRESS_VERSION`  in windows/build/build-python.ps1 (latest: PyPI)
  * OpenSSL       `MSYS2_OPENSSL_PKG`  in the workflow: the MSYS2 package that is pinned to the 3.x series (nginx.exe links it
                  statically); the newest package of the same major in the MSYS2 archive is the "latest". A new major
                  (4.x) is deliberately not reported: moving to it is a decision, see windows/patches/README.md

Not checked, because they float on purpose: conan (a range), the other MSYS2 packages, Inno Setup, pywin32/colorama/tzdata,
the CPython patch version, the runner image.

An update you looked at and decided against is listed in windows/build/tools-watch.ignore
(`<name> <version>  # why`); it stays quiet until an even newer version appears.

Usage: python windows/build/tools-watch.py [--report FILE] [--offline]
  --report FILE  write a Markdown report of the updates to FILE (only when there are any)
  --offline      only read the pins and fail if one is missing (what the unit test does)
Exit code:
  0   everything checked, nothing to do
  10  updates available
  11  nothing found, but a lookup failed (frippery.org and GitHub are not always reachable): not proof that all is current
  2   a pin could not be found: this script is out of date
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IGNORE_FILE = Path(__file__).with_name("tools-watch.ignore")
WORKFLOW = ".github/workflows/bunkerweb-windows.yml"
BUILD_PYTHON = "windows/build/build-python.ps1"

USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/[^@\s]*)?@([^\s#]+)", re.M)


@dataclass(frozen=True)
class Pin:
    kind: str  # action | tool | package
    name: str  # actions/checkout, winsw, busybox-w32, waitress
    current: str  # the pinned ref or version, as written
    where: str  # file it was read from


@dataclass
class Update:
    pin: Pin
    latest: str
    url: str


def version_tuple(text):
    """'v7.0.1' -> (7, 0, 1); None when there is no version in it (a branch name, a commit SHA)."""
    match = re.match(r"^v?(\d+(?:\.\d+)*)$", text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def action_is_outdated(pinned, latest):
    """@v7 follows every v7.x: only a new major counts. An exact pin (@v7.0.1) counts every newer version."""
    pinned_v, latest_v = version_tuple(pinned), version_tuple(latest)
    if pinned_v is None or latest_v is None:
        return False  # pinned to a SHA or a branch: nothing to compare
    if len(pinned_v) == 1:
        return latest_v[0] > pinned_v[0]
    return latest_v > pinned_v


def version_is_newer(current, latest):
    current_v, latest_v = version_tuple(current), version_tuple(latest)
    return current_v is not None and latest_v is not None and latest_v > current_v


# ------------------------------------------------------------------------------------------------ the pins
def read_text(path):
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def find_pins(repo=REPO):
    """All pins of this repository. Raises LookupError listing every pin that is missing (also when a file is gone)."""
    pins, missing = [], []

    sources = sorted((repo / ".github" / "workflows").glob("*.yml")) + sorted((repo / ".github" / "actions").glob("*/action.yml"))
    seen = set()
    for source in sources:
        for name, ref in USES_RE.findall(read_text(source)):
            if (name, ref) not in seen:
                seen.add((name, ref))
                pins.append(Pin("action", name, ref, source.relative_to(repo).as_posix()))
    if not any(pin.kind == "action" for pin in pins):
        missing.append("no `uses:` of a GitHub Action found in .github/")

    workflow = read_text(repo / WORKFLOW)
    match = re.search(r"^\s*WINSW_URL:\s*\S*/winsw/winsw/releases/download/v([\d.]+)/", workflow, re.M)
    if match:
        pins.append(Pin("tool", "winsw", match.group(1), WORKFLOW))
    else:
        missing.append(f"WINSW_URL (winsw/winsw release URL) in {WORKFLOW}")
    match = re.search(r"^\s*BUSYBOX_URL:\s*\S*busybox-w64-FRP-(\d+)-g[0-9a-f]+\.exe", workflow, re.M)
    if match:
        pins.append(Pin("tool", "busybox-w32", match.group(1), WORKFLOW))
    else:
        missing.append(f"BUSYBOX_URL (busybox-w64-FRP-<n>-g<hash>.exe) in {WORKFLOW}")

    match = re.search(r"^\s*MSYS2_OPENSSL_PKG:\s*mingw-w64-ucrt-x86_64-openssl-(\d+(?:\.\d+)*-\d+)-any\.pkg\.tar\.zst\s*$", workflow, re.M)
    if match:
        pins.append(Pin("package", "msys2-openssl", match.group(1), WORKFLOW))
    else:
        missing.append(f"MSYS2_OPENSSL_PKG (mingw-w64-ucrt-x86_64-openssl-<version>-<rel>-any.pkg.tar.zst) in {WORKFLOW}")

    match = re.search(r"^\$WAITRESS_VERSION\s*=\s*'([\d.]+)'", read_text(repo / BUILD_PYTHON), re.M)
    if match:
        pins.append(Pin("package", "waitress", match.group(1), BUILD_PYTHON))
    else:
        missing.append(f"$WAITRESS_VERSION in {BUILD_PYTHON}")

    if missing:
        raise LookupError("pins this script expects but did not find (renamed? update tools-watch.py):\n  - " + "\n  - ".join(missing))
    return pins


def read_ignore(path=IGNORE_FILE):
    ignored = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split("#", 1)[0].split()
            if len(fields) >= 2:
                ignored.add((fields[0], fields[1].lstrip("v")))
    return ignored


# ------------------------------------------------------------------------------------------------ lookups
def fetch(url):
    headers = {"User-Agent": "bunkerweb-windows-tools-watch", "Accept": "application/json, text/html"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def latest_release(repo):
    data = json.loads(fetch(f"https://api.github.com/repos/{repo}/releases/latest"))
    return data["tag_name"], data["html_url"]


def latest_busybox():
    """frippery.org lists the releases newest first. It answers 403 to GitHub's runners at times: use the tags then."""
    try:
        match = re.search(r"FRP-(\d+)-g[0-9a-f]+", fetch("https://frippery.org/busybox/release-notes/"))
        if match:
            return match.group(1), "https://frippery.org/busybox/release-notes/"
    except (urllib.error.URLError, OSError):
        pass
    tags = json.loads(fetch("https://api.github.com/repos/rmyorston/busybox-w32/tags?per_page=50"))
    numbers = [int(m.group(1)) for tag in tags if (m := re.match(r"^FRP-(\d+)-g", tag["name"]))]
    if not numbers:
        raise LookupError("no FRP-<n> tag found")
    return str(max(numbers)), "https://github.com/rmyorston/busybox-w32/tags"


def latest_pypi(name):
    data = json.loads(fetch(f"https://pypi.org/pypi/{name}/json"))
    return data["info"]["version"], f"https://pypi.org/project/{name}/"


def package_version_key(text):
    """'3.6.5-1' -> (3, 6, 5, 1): version and pkgrel compared together."""
    return tuple(int(part) for part in re.split(r"[.-]", text))


def newest_msys2_openssl(current):
    """Newest package of the pinned major in the MSYS2 archive (which keeps old versions)."""
    listing = fetch("https://repo.msys2.org/mingw/ucrt64/")
    found = re.findall(r'href="mingw-w64-ucrt-x86_64-openssl-(\d+(?:\.\d+)*-\d+)-any\.pkg\.tar\.zst"', listing)
    major = package_version_key(current)[0]
    candidates = [version for version in found if package_version_key(version)[0] == major]
    if not candidates:
        raise LookupError(f"no openssl {major}.x package found in the MSYS2 archive")
    return max(candidates, key=package_version_key), "https://repo.msys2.org/mingw/ucrt64/"


def check(pins, ignored):
    """Returns (updates, warnings)."""
    updates, warnings = [], []
    for pin in pins:
        try:
            if pin.kind == "action":
                latest, url = latest_release(pin.name)
                outdated = action_is_outdated(pin.current, latest)
            elif pin.name == "winsw":
                latest, url = latest_release("winsw/winsw")
                latest = latest.lstrip("v")
                outdated = version_is_newer(pin.current, latest)
            elif pin.name == "busybox-w32":
                latest, url = latest_busybox()
                outdated = int(latest) > int(pin.current)
            elif pin.name == "msys2-openssl":
                latest, url = newest_msys2_openssl(pin.current)
                outdated = package_version_key(latest) > package_version_key(pin.current)
            elif pin.name == "waitress":
                latest, url = latest_pypi("waitress")
                outdated = version_is_newer(pin.current, latest)
            else:
                raise LookupError(f"no lookup for {pin.name}")
        except (urllib.error.URLError, OSError, ValueError, KeyError, LookupError) as exc:
            warnings.append(f"{pin.name}: could not check ({type(exc).__name__}: {exc})")
            continue
        if outdated and (pin.name, latest.lstrip("v")) not in ignored:
            updates.append(Update(pin, latest, url))
    return updates, warnings


# ------------------------------------------------------------------------------------------------ report
def markdown(updates, warnings):
    lines = [
        "Pinned build tools or GitHub Actions of this repository have a newer release. Found by the nightly "
        "**Port check** (`tools-watch` job, `windows/build/tools-watch.py`).",
        "",
        "| Component | Pinned | Latest | Pinned in |",
        "|---|---|---|---|",
    ]
    for update in updates:
        lines.append(f"| [{update.pin.name}]({update.url}) | `{update.pin.current}` | `{update.latest}` | `{update.pin.where}` |")
    lines += [
        "",
        "How to proceed: read the release notes, change the pin (for WinSW, busybox and the MSYS2 OpenSSL package the URL/file name **and** its SHA-256 together, "
        "in the workflow `env`), bump `CACHE_VERSION` when a tool changes, and let the main workflow run. A release you decide "
        "against goes into `windows/build/tools-watch.ignore` with the reason; this issue then closes by itself.",
    ]
    if warnings:
        lines += ["", "Could not be checked this time:", ""] + [f"- {warning}" for warning in warnings]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", help="write a Markdown report of the updates to this file")
    parser.add_argument("--offline", action="store_true", help="only read the pins")
    args = parser.parse_args(argv)

    try:
        pins = find_pins()
    except LookupError as exc:
        print(f"tools-watch: {exc}", file=sys.stderr)
        return 2
    print(f"{len(pins)} pins found: " + ", ".join(sorted({pin.name for pin in pins})))
    if args.offline:
        return 0

    updates, warnings = check(pins, read_ignore())
    for warning in warnings:
        print(f"::warning::{warning}")
    if not updates:
        if warnings:
            print("No update found, but some lookups failed (see above).")
            return 11
        print("Everything is up to date.")
        return 0
    for update in updates:
        print(f"UPDATE {update.pin.name}: {update.pin.current} -> {update.latest}  ({update.pin.where})  {update.url}")
    if args.report:
        Path(args.report).write_text(markdown(updates, warnings), encoding="utf-8")
    return 10


if __name__ == "__main__":
    sys.exit(main())
