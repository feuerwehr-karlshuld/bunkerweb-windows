#!/usr/bin/env python3
"""Hazard scanner: finds Linux-isms in the *patched* BunkerWeb sources and compares them with a committed baseline.

The port cannot know what a new upstream version starts to call. This scanner turns "upstream added a new libc
function / shell command / native library / fork / signal / absolute path check" into a visible diff in CI instead of
a runtime failure on a Windows server. It does not judge: whatever exists in the baseline was reviewed once.

  python windows/build/scan-hazards.py <patched-src>            # compare with windows/build/hazards.baseline.txt
  python windows/build/scan-hazards.py <patched-src> --update   # rewrite the baseline after reviewing the report

Exit code 1 when a hazard is new or occurs more often than in the baseline. Fewer occurrences are only reported.
The baseline holds counts per (file, hazard), not line numbers, so unrelated edits do not churn it.
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

BASELINE = Path(__file__).resolve().parent / "hazards.baseline.txt"
OVERLAY = Path(__file__).resolve().parents[1] / "overlay" / "src"

# Third party trees under deps/src whose Lua code is shipped; everything else under deps/ is C or Python sources that
# are built (or installed from hashed wheels) and not scanned here.
SCAN_DEPS = re.compile(r"^deps/src/(lua-|luajit-)")
SKIP_PARTS = {
    "coreruleset-v3", "coreruleset-v4", "node_modules", "__pycache__", "static", "locales", "tests", "test", "t", "spec",
    "examples", "example", "docs", "doc", "bench", "benchmark", "linux", "all-in-one", "autoconf", "rockspec",
}
SUFFIXES = {".lua": "lua", ".py": "py", ".conf": "conf", ".sh": "sh", ".json": "json"}

# name -> (languages, regex). A capture group 1 makes the matched text part of the key, so a *new* symbol/command/library
# is reported even in a file that already had other hits.
HAZARDS = {
    # libc-style (lowercase) symbols only: nginx (ngx_*) and OpenSSL (UPPER_*, i2d_*, ...) exports are part of nginx.exe
    "ffi.C symbol": (
        {"lua"},
        r"\b(?:ffi\.)?C\.(?!(?:ngx_|i2d_|d2i_|sk_|ossl_|lua|resty_)\w)([a-z_][a-z0-9_]*)",
    ),
    "ffi.load": ({"lua"}, r"ffi\.load\(\s*[\"']([^\"']+)"),
    "/proc": ({"lua", "py", "sh", "conf"}, r"(/proc/[\w/.-]+)"),
    "/dev": ({"lua", "py", "sh", "conf"}, r"(/dev/(?!null\b)[\w/.-]+)"),
    "unix socket": ({"lua", "py", "conf"}, r"(unix:|AF_UNIX|mkfifo)"),
    "os.uname": ({"lua", "py", "conf"}, r"\b(os\.uname|platform\.(?:uname|system|release))"),
    "fork": ({"py"}, r"\b(os\.fork|forkserver|set_start_method|ProcessPoolExecutor|multiprocessing\.\w+)"),
    "posix module": ({"py"}, r"^\s*(?:import|from)\s+(fcntl|pwd|grp|resource|termios|syslog|pty|tty)\b"),
    "uid/gid": ({"py", "lua"}, r"\b(chown|lchown|getuid|geteuid|getgid|setuid|setgid|getpwnam|getgrnam|initgroups|umask)\b"),
    "isabs": ({"py"}, r"\b(is_absolute|isabs)\b"),
    "signal": ({"py", "lua"}, r"\b(SIG(?:HUP|USR1|USR2|TERM|KILL|CHLD|PIPE|ALRM|QUIT|INT)|os\.kill|killpg|resty\.signal)\b"),
    "process spawn": ({"py", "lua", "sh"}, r"\b(io\.popen|os\.execute|os\.system|subprocess\.(?:Popen|run|call|check_output|check_call)|os\.exec\w*|os\.spawn\w*|shell=True)"),
    "shell command": (
        {"py", "lua"},
        r"[\"'`](?:sudo )?(cp|mv|rm|tar|chmod|chown|ln|find|cat|grep|sed|awk|openssl|curl|wget|nginx|ps|pgrep|pkill|systemctl|service|"
        r"mkdir|touch|tee|sort|uniq|wc|date|id|whoami|getent|df|du|free|uname|hostname|sh|bash|dd|ldd|file|install)\s",
    ),
    "executable path": ({"py", "lua", "sh", "conf"}, r"[\"'](/(?:usr/)?(?:local/)?(?:s?bin)/\w[\w.-]*)"),
    "/tmp": ({"py", "lua", "sh", "conf"}, r"(/tmp/[\w.-]*)"),
    "path separator": ({"py"}, r"(split\(\":\"\)|\":\"\.join|split\(':'\)|':'\.join)"),
    "symlink/chmod": ({"py"}, r"\b(os\.symlink|symlink_to|os\.link|os\.chmod|\.chmod\(|os\.mkfifo|os\.readlink)\b"),
    "write open (lua)": ({"lua"}, r"\bopen\([^)\n]*,\s*[\"']([wa][+bx]*)[\"']"),
    "write open (py)": ({"py"}, r"\b(?:open\(\s*(?:[^)\n]*,\s*)?[\"']([wa][+tx]*)[\"']|(write_text)\()"),
    "os resource": ({"py"}, r"\b(os\.sysconf|os\.getloadavg|sched_getaffinity|os\.sched_\w+|os\.nice|os\.setsid|os\.setpgid|os\.getpgid)"),
    "nginx directive": ({"conf"}, r"\b(http3|quic_\w+|load_module|reuseport|use epoll|worker_cpu_affinity|ssl_engine|aio threads|sendfile)\b"),
}
COMPILED = {name: (langs, re.compile(rx, re.M)) for name, (langs, rx) in HAZARDS.items()}


def candidates(src, overlay=OVERLAY):
    ours = {item.relative_to(overlay).as_posix() for item in overlay.rglob("*") if item.is_file()} if overlay.is_dir() else set()
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        rel = path.relative_to(src)
        posix = rel.as_posix()
        if posix in ours:  # the port's own files necessarily use every Win32 API
            continue
        if rel.parts[0] == "deps":
            if not SCAN_DEPS.match(posix):
                continue
            parts = set(rel.parts[3:])
        else:
            parts = set(rel.parts)
        if parts & SKIP_PARTS:
            continue
        yield posix, path, SUFFIXES[path.suffix]


def scan(src):
    """Return Counter {(file, 'hazard[: detail]'): count} and {key: first excerpt}."""
    counts = Counter()
    excerpts = {}
    for posix, path, lang in candidates(src):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, (langs, rx) in COMPILED.items():
            if lang not in langs:
                continue
            for match in rx.finditer(text):
                detail = next((g for g in match.groups() if g), "")
                key = (posix, f"{name}: {detail}" if detail else name)
                counts[key] += 1
                if key not in excerpts:
                    line_start = text.rfind("\n", 0, match.start()) + 1
                    line_end = text.find("\n", match.end())
                    excerpts[key] = text[line_start: line_end if line_end != -1 else None].strip()[:140]
    return counts, excerpts


def read_baseline(path):
    counts = Counter()
    if not path.is_file():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        count, file, hazard = line.split("\t")
        counts[(file, hazard)] = int(count)
    return counts


def write_baseline(path, counts):
    lines = [
        "# Hazards in the patched BunkerWeb sources, reviewed. Generated by windows/build/scan-hazards.py --update.",
        "# Format: count<TAB>file<TAB>hazard. A new line or a higher count fails the port check: review it, then --update.",
    ]
    lines += [f"{count}\t{file}\t{hazard}" for (file, hazard), count in sorted(counts.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("src", type=Path, help="patched copy of bunkerweb/src")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--update", action="store_true", help="rewrite the baseline with the current findings")
    args = parser.parse_args()

    counts, excerpts = scan(args.src.resolve())
    if args.update:
        write_baseline(args.baseline, counts)
        print(f"baseline written: {args.baseline} ({len(counts)} entries, {sum(counts.values())} occurrences)")
        return 0

    baseline = read_baseline(args.baseline)
    new = sorted(key for key in counts if key not in baseline)
    more = sorted(key for key in counts if key in baseline and counts[key] > baseline[key])
    gone = sorted(key for key in baseline if key not in counts or counts[key] < baseline[key])

    for title, keys in (("NEW hazards", new), ("MORE occurrences than in the baseline", more)):
        if keys:
            print(f"\n{title}:")
            for key in keys:
                before = baseline.get(key, 0)
                print(f"  {key[0]}: {key[1]}  ({before} -> {counts[key]})\n      e.g. {excerpts[key]}")
    if gone:
        print(f"\n{len(gone)} baseline entries no longer (fully) present (informational, run --update to prune):")
        for key in gone[:20]:
            print(f"  {key[0]}: {key[1]}  ({baseline[key]} -> {counts.get(key, 0)})")
        if len(gone) > 20:
            print(f"  ... and {len(gone) - 20} more")

    if new or more:
        print(
            f"\nHazard scan: {len(new)} new, {len(more)} increased. For each: does it work on Windows (nginx.exe, busybox sh, "
            "Python 3.14, LuaJIT/MinGW)? If it needs a shim or patch, add it (windows/overlay, windows/patches, "
            "apply-windows-port.py). Then run: python windows/build/scan-hazards.py <src> --update",
            file=sys.stderr,
        )
        return 1
    print(f"Hazard scan OK: {sum(counts.values())} known occurrences in {len(counts)} (file, hazard) pairs, nothing new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
