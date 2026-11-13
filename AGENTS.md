# AGENTS.md: orientation for coding agents and contributors

Read [`README.md`](README.md) (goal, operator guide) and [`windows/README.md`](windows/README.md) (how the port works) for the
full picture. This file is the short version of what you need to **work on** the project without breaking it.

## What this project is

A **native Windows Server build of [BunkerWeb](https://github.com/bunkerity/bunkerweb)** (nginx + Lua + ModSecurity reverse
proxy/WAF with a Python scheduler, web UI and Let's Encrypt automation), delivered as an Inno Setup installer that runs four
Windows services. It is built entirely by GitHub Actions from the upstream sources plus the port in `windows/`.

The target use case (it drives priorities): a Windows Server without containers/VMs/WSL acting as reverse proxy for
HTTP backends on the same server and in the LAN, Let's Encrypt via **HTTP-01** (DNS is manual), configuration as code
(`variables.env` in git), UI optional.

## Hard rules

1. **Never modify anything in `bunkerweb/`.** It is a pinned git submodule (upstream tag, currently v1.6.17).
   All Windows changes are applied to a *copy* (`build/src`) at build time by `windows/build/apply-windows-port.py` or come from
   `windows/overlay/`. If upstream code must change, add an anchored replacement there.
2. **CI is the only test harness.** There is no supported local build and no Windows machine in the dev loop. The pipeline
   runs on `windows-2022`. You cannot verify a change by running it; read logs, reason carefully, and say so when something
   is unverified. The human provides CI logs (`gh run view <id> --log-failed`, or the full `run.log`).
3. **Do not commit, push, tag or create branches unless the user asks.** Tags `v<bunkerweb-version>-win<N>` trigger releases.
4. **Licence is AGPLv3** (upstream). The repo must stay public, or the source must otherwise be available to users of the
   installer. Do not add code under incompatible licences.
5. Keep **forward slashes / POSIX-style absolute paths** where BunkerWeb expects them. Everything lives under the single root
   `C:\bunkerweb\` (mirroring the Linux sub-layout: `usr\`, `etc\`, `var\`). `apply-windows-port.py` (step 13) rewrites
   BunkerWeb's own paths from `/etc/bunkerweb` to `/bunkerweb/etc/bunkerweb`; the services run with working directory
   `C:\bunkerweb` (on `C:`), and Windows resolves the drive-less rooted path against the drive. Never introduce paths in
   `C:\usr`, `C:\etc`, `C:\var` or `C:\tmp`, and put final `/bunkerweb/...` paths in `windows/overlay/` files (they are not
   rewritten).
6. Everything is **unofficial**: never direct users to Bunkerity for problems with this port.

## Repository map

```
bunkerweb/                  upstream submodule, READ-ONLY
README.md                   operator guide (install, proxy, Let's Encrypt, config as code, ops)  <- keep in sync with behaviour
windows/README.md           engineering doc: Linux->Windows mapping table, layouts, build pipeline, upgrade procedure
windows/build/
  apply-windows-port.py     anchored, COUNTED text replacements on the copy of bunkerweb/src; fails listing every stale anchor
  build-modsecurity.ps1     libModSecurity.dll, MSVC 2022 + conan + CMake (ModSecurity's own build/win32)
  build-native.sh           MSYS2 UCRT64: LuaJIT, nginx.exe with ALL modules static, Lua C modules, Lua libs, runtime DLLs
  build-python.ps1          private CPython copy + hash-pinned deps (--require-hashes --no-deps --target) + certbot DNS alias patch
  prepare-requirements.py   merges BunkerWeb's per-component pip-compile files into one hashed file (drops uvloop)
  stage.ps1                 assembles the tree mirroring C:\bunkerweb\, verifies required files, writes BUILDINFO
windows/installer/bunkerweb.iss   Inno Setup 6 script (services, PATH, firewall, config kept on uninstall)
windows/overlay/            files copied into the build: wincompat.lua (Lua/FFI shim), python-site/ (fcntl, pwd, grp,
                            sitecustomize), bwsvc.py (service runner), bwctl.py (operator CLI), bwctl.cmd, bwcli.cmd, certbot entry
windows/service/*.xml       WinSW 2.x service definitions: bunkerweb-nginx, -scheduler, -ui, -api
.github/workflows/bunkerweb-windows.yml   pipeline (below)
.github/actions/prepare-src/action.yml    robocopy bunkerweb/src -> build/src, then run apply-windows-port.py
```

## Pipeline (`.github/workflows/bunkerweb-windows.yml`)

`modsecurity` (MSVC/conan, slow, cached) -> `native` (MSYS2/GCC, needs the modsecurity artifact) and `python` (parallel) ->
`package` (stage, Inno Setup, `SHA256SUMS.txt`) -> `smoke-test` (silent install, proxy a request, bwctl checks, UI on :7000,
restart, uninstall) -> `release` (tags only).

- Every job: `core.autocrlf false` + `core.longpaths true`, checkout with submodules, setup-python, then `prepare-src`.
  LF endings matter: the anchored replacements match exact text including newlines.
- Downloads are pinned and SHA-256 verified (busybox-w32, WinSW 2.12.0 `WinSW.NET461.exe`). Change URL and hash together.
- `PYTHON_VERSION` is `3.14` (what BunkerWeb's Linux packages use). If a dependency has no cp314 wheel, switch to `3.13`.
- Release tag format `v<VERSION>-win<N>`; `<VERSION>` must equal `bunkerweb/src/VERSION`.
- Artifacts: `modsecurity`, `native`, `python`, `stage`, `installer`, `smoke-test-logs` (first place to look after a failure).

## How to work on a change

- **A build step fails**: find the first real error in the log (`##[error]` is only the exit code; the cause is above it).
  Fix in `windows/`, never in `bunkerweb/`. Typical fixes are one more anchored replacement in `apply-windows-port.py`
  (`rep(file, old, new, count)` fails the build if the occurrence count differs, which is intended), a compiler/linker flag in
  `build-native.sh`, or a shim in `windows/overlay/`.
- **Upgrading BunkerWeb**: follow "Upgrading BunkerWeb" in `windows/README.md`. Expect stale anchors, new nginx modules or Lua
  libraries in `src/deps/deps.json`, and Python requirements without Windows wheels.
- **Runtime behaviour** (services, reload, certbot, shims) can only be exercised by the smoke test. When you add behaviour,
  add a check to the smoke test if it is cheap. When you cannot test, document the risk.
- Keep `README.md` consistent with reality: ports, paths, service names, `bwctl` commands, defaults. The operator guide is
  part of the deliverable.
- Settings defaults quoted in the guide were checked against the 1.6.17 sources (`LETS_ENCRYPT_CHALLENGE=http`,
  `AUTO_REDIRECT_HTTP_TO_HTTPS=yes`, `DISABLE_DEFAULT_SERVER=no`, `USE_BACKUP=yes`, `USE_MODSECURITY_GLOBAL_CRS=no`,
  `USE_BUNKERNET`/`USE_DNSBL=yes`). Re-verify after upgrades.

## Gotchas learned so far

- **Windows-only Python deps are missing from the Linux lock files** (`pywin32` for certbot, `colorama`, `tzdata` for psycopg/zoneinfo).
  The hashed install
  therefore uses `--no-deps` and a second unhashed step installs them; `pip check` reports other gaps. pywin32 lives in a
  `--target` dir, so `sitecustomize.py` processes its `.pth` file explicitly.
- **nginx for Windows does not define `NGX_HAVE_VARIADIC_MACROS`**; code relying on `dd(...)`-style macros compiles arguments that
  only exist in debug builds (fixed for modsecurity-nginx in step 12b of `apply-windows-port.py`). Expect similar breakage in other
  third-party nginx modules.
- nginx's `configure` skips system OpenSSL/PCRE2/zlib on win32; the port patches that (step 12). OpenSSL is linked whole-archive
  into `nginx.exe` so LuaJIT FFI (`ffi.C`) finds `SSL_*`/`EVP_*`.
- nginx for Windows: one worker, `select()` event model (`FD_SETSIZE=4096` at build time), no reuseport, no HTTP/3, signals via
  named events `Global\ngx_<signal>_<pid>`, files opened with `FILE_SHARE_DELETE`.
- libModSecurity is built with MSVC (its supported Windows toolchain), nginx with MinGW/UCRT; they meet through the plain
  C API (`msc_*`) and a generated import library.
- The submodule contains a few symlinks (mbedtls examples, brotli research); harmless on Windows.
- Let's Encrypt data lives in `C:\bunkerweb\var\cache\bunkerweb\letsencrypt` (`/var/cache/bunkerweb/letsencrypt` upstream).
- Upstream UI/API use gunicorn; the port uses waitress (UI) and uvicorn without uvloop (API).

## Current state

*Last updated 2026-11-13. Update this section whenever the pipeline state changes.*

- **Third CI run** (after the path refactor and the nginx `binary` fix): `modsecurity`, `python` and `native` **passed**, so the
  nginx link works. `package` failed in "Download busybox-w32 and WinSW": `frippery.org` answered `403 Forbidden` to the runner
  (URL and hash are correct and reachable from elsewhere). Fix written: retries plus a fallback to the Chocolatey `busybox`
  6354.0.0 package (`tools/busybox64.exe`, identical SHA-256), not yet re-run. The `pip check` warning was `psycopg requires
  tzdata`; `tzdata` is now installed with the other Windows-only deps, not yet re-run. `stage`, installer, `smoke-test` unseen.
- **Second CI run** (before the path refactor): `modsecurity` and `python` **passed** (`pip check` annotation only). `native`
  got through LuaJIT, the Lua modules, nginx configure and the compilation of all nginx sources, then failed in `make` with
  `No rule to make target 'docs/man/nginx.8'`: BunkerWeb's vendored nginx has no `docs/` directory and nginx's default target
  builds the man page. Fix written (`make -f objs/Makefile binary`), not yet re-run, so the link step is still unseen.
- **First CI run** (push of the initial port):
  - `modsecurity`: **passed** (about 21 minutes, conan cache now filled).
  - `python`: failed, certbot's Windows-only dependency `pywin32` was not hash-pinned. Fix written (see Gotchas), not yet re-run.
  - `native`: failed compiling modsecurity-nginx (`clcf` undeclared via `dd()`). Fix written (step 12b), not yet re-run.
  - `package`, `smoke-test`, `release`: never reached.
- Since then: all GitHub Actions bumped to Node 24 majors (`checkout@v7`, `setup-python@v7`, `cache@v6`,
  `upload-artifact@v7`, `download-artifact@v8`, `action-gh-release@v3`); the repo was moved out of the old `reverse-proxy`
  repository into this one. The workflow has not run in this repo yet.
- **Path refactor (unverified):** all `C:\usr`, `C:\etc`, `C:\var`, `C:\tmp` locations moved below `C:\bunkerweb\`
  (build-time path rewrite in `apply-windows-port.py` step 13, `C:/bunkerweb/...` compiled into nginx, service working directory
  `C:\bunkerweb`, installer, WinSW XMLs, `bwsvc.py`, `bwctl.*`, smoke test). Only the rewrite itself was exercised, on a copy of
  `bunkerweb/src` on Linux (all changed `.py` files parse, second pass is a no-op). Nothing has run in CI since. Watch for: rewrite
  coverage (the step prints `NOTE` lines for skipped Linux paths, only `/usr/local/etc/nginx/nginx.conf` in `api.lua` is expected),
  nginx's compiled-in paths, and the new smoke-test step "Nothing is installed outside C:\bunkerweb".
- **Next:** push, run the workflow, iterate on failures. Most likely trouble spots, roughly in order: nginx link step
  (OpenSSL whole-archive, libModSecurity import lib, module linking), Python 3.14 wheel availability, `stage.ps1`
  required-files check, installer script, runtime behaviour of the shims and `bwsvc.py` in the smoke test (reload via named
  events, certbot invocation, WinSW service dependencies).
- After a green run: tag `v1.6.17-win1`, then trial on a non-critical server including a Let's Encrypt *staging* issuance.
- Operator guide and installer have never run on a real server. Do not describe anything as "tested" beyond what the smoke test
  in a green CI run covers.

## Out of scope / do not do

- Do not turn this into a fork of BunkerWeb or vendor modified upstream files into the repo.
- Do not add container, VM or WSL based alternatives: they are explicitly ruled out by the requirements.
- Do not store secrets (tokens, passwords, API keys) in the repo. The operator's `variables.env` with their real hosts and
  backends lives in their own private configuration repo. The one real hostname in README section 4 is an intentional example.
