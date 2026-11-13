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
3. **Do not commit, push, tag or create branches unless the user asks.** Releases and their tags are created by the `release` job, not by hand.
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
  patch-modsecurity.py      same mechanism, but ONLY for what build-modsecurity.ps1 compiles (deps/src/modsecurity); separate so
                            the slow `modsecurity` job's cache key does not depend on the rest of the port
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
.github/actions/prepare-src/action.yml    robocopy bunkerweb/src -> build/src, then patch-modsecurity.py + apply-windows-port.py (input `scope: modsecurity` = only the first)
```

## Pipeline (`.github/workflows/bunkerweb-windows.yml`)

`modsecurity` (MSVC/conan, slow, cached) -> `native` (MSYS2/GCC, needs the modsecurity artifact) and `python` (parallel) ->
`package` (stage, Inno Setup, `SHA256SUMS.txt`) -> `smoke-test` (silent install, proxy a request, bwctl checks, UI on :7000,
restart, uninstall) -> `release` (main only; smoke test green: release `v<bunkerweb-version>`, e.g. `v1.6.17`, marked latest; smoke test
red: **draft** `v<bunkerweb-version>-RC` with installer and `smoke-test-logs.zip`. Each run deletes the previous release of the
same kind (same tag and draft state, incl. its tag and assets) and recreates it on the built commit; drafts and real releases
never replace each other).

- **Build caches:** `modsecurity`, `native` and `python` cache their output directory (`build/modsecurity|native|python`)
  and `package` caches the finished installer (`dist/`: setup exe + `SHA256SUMS.txt`) with `actions/cache`. Key = job + `CACHE_VERSION` (workflow `env`) + the commit of the `bunkerweb` submodule
  (`git rev-parse HEAD:bunkerweb`) + `hashFiles(...)` of the repository files that influence that output. On an exact hit the build
  steps (and, for `native`, MSYS2 and the modsecurity artifact download) are skipped, the artifact is still uploaded for the next
  job. **When you add an input to a build script, add it to that job's `hashFiles(...)`**, otherwise a stale cache is served.
  What is *not* in the keys (workflow/MSYS2 package list, runner image, unpinned tools such as conan, pywin32, tzdata): bump
  `CACHE_VERSION`. `native`'s key includes modsecurity's inputs because nginx links its output; `modsecurity`'s key hashes
  `patch-modsecurity.py`, **not** `apply-windows-port.py` (its job runs `prepare-src` with `scope: modsecurity`): a patch that changes
  what `build-modsecurity.ps1` compiles must go into `patch-modsecurity.py`, everything else into `apply-windows-port.py`;
  `python`'s key contains the exact
  CPython version from `setup-python`. Cache entries are scoped per branch, unused entries
  expire after 7 days. `smoke-test` is never cached.
    `package`'s key = `CACHE_VERSION` + version + port revision + bunkerweb commit + CPython version + `BUSYBOX_SHA256` +
    `WINSW_SHA256` + `hashFiles('windows/**', '.github/actions/**')`. On a hit, prepare-src, both artifact downloads, the tool
    download, stage and ISCC are skipped and the restored `dist/` is uploaded as the `installer` artifact (the `stage` artifact
    does not exist then). The `Version` step still runs, so the `release` job is unaffected. The revision is the `port_revision`
    input of a manual run, else `1`. **The workflow file itself is not in any key:** changing the package,
    stage or ISCC steps or the tool download logic needs a `CACHE_VERSION` bump; editing only smoke-test steps keeps the hit.
  The cache is saved by an explicit `actions/cache/save` step right after a successful build (the restore is
  `actions/cache/restore`), so a failing artifact upload or failing later jobs do not lose it. The conan packages of the
  ModSecurity build are saved even if that build fails (a partial conan cache is kept for that key until the
  `conanfile.txt` changes).

- Every job: `core.autocrlf false` + `core.longpaths true`, checkout with submodules, setup-python, then `prepare-src`.
  LF endings matter: the anchored replacements match exact text including newlines.
- Downloads are pinned and SHA-256 verified (busybox-w32, WinSW 2.12.0 `WinSW.NET461.exe`). Change URL and hash together.
- `PYTHON_VERSION` is `3.14` (what BunkerWeb's Linux packages use). If a dependency has no cp314 wheel, switch to `3.13`.
- Release/tag format: `v<VERSION>` (release) and `v<VERSION>-RC` (draft); `<VERSION>` is `bunkerweb/src/VERSION` of the submodule.
  The installer file name still carries the port revision (`-win<N>`).
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
- **`ffi.C` on Windows only sees the exe exports, lua51.dll, the CRT, kernel32/user32/gdi32**: winsock (`inet_pton`, `ntohl`) needs
  `ffi.load("ws2_32")`, and `setenv`/`unsetenv`/`environ` do not exist in the UCRT (`_putenv_s`, `__p__environ`). Such calls load fine
  and fail on the first request. Check every `ffi.C`/`cdef` use of new Lua libraries after an upgrade (step 7/7b).
- The submodule contains a few symlinks (mbedtls examples, brotli research); harmless on Windows.
- Let's Encrypt data lives in `C:\bunkerweb\var\cache\bunkerweb\letsencrypt` (`/var/cache/bunkerweb/letsencrypt` upstream).
- Upstream UI/API use gunicorn; the port uses waitress (UI) and uvicorn without uvloop (API).
- **Python >= 3.13: a rooted path without drive (`/bunkerweb/...`, what step 13 produces) is not absolute on Windows**
  (`Path.is_absolute()`/`os.path.isabs()` are False). Upstream validators using them (`cache_restore._check_path`, tar link
  checks, UI log/plugin path checks) are patched in step 12d; watch for further `is_absolute`/`isabs` uses after an upgrade.
- **No `fork` on Windows:** the config generator (`Templator.render`) forced the fork start method and used a
  `ProcessPoolExecutor`; step 12c renders in one worker thread instead. Any other `multiprocessing` use needs the same care.
- Child Python processes spawned by BunkerWeb with an explicit `env=` lose `PYTHONUTF8`/`PYTHONIOENCODING` unless
  `sitecustomize.py` re-adds them (`_KEEP_ENV`); without them emoji log lines fail with cp1252 `UnicodeEncodeError`.
- The smoke-test job's `Diagnostics` step prints all service/BunkerWeb logs into the run log (grouped per file).

## Current state

*Last updated 2026-11-13. Update this section whenever the pipeline state changes.*

- **Eighth CI run:** the seventh run's `"w+b"` fix **worked**: `healthz` became `ok` ~20 s after start, firewall rule and all five
  listeners match the configured ports, and the reverse proxy to the backend returned the marker (HTTP 200). The first failing step was
  "Unknown hosts are rejected by the default server", a **test bug**, not a port bug: the default server (`DISABLE_DEFAULT_SERVER=no`)
  correctly answered `301` (HTTP -> HTTPS redirect, access log confirms), and `Invoke-WebRequest` followed it to
  `https://unknown.smoke.test:<port>/`, which the runner cannot resolve (`No such host is known`). Adding `-MaximumRedirection 0`
  was not enough: PowerShell 7 throws on any 3xx with it. Fix written, not yet re-run: that step and "Web UI answers on its
  configured port" (UI redirects to login/setup too) now use `curl.exe` (`-w '%{http_code}'`, no redirect following). The steps after
  it (bwctl, UI, restart, uninstall) have not been seen yet.
- **Seventh CI run:** the sixth run's fix **worked** (all plugins load, nginx runs, the scheduler generates and pushes). `smoke-test`
  failed: `healthz` stayed `loading` for 3 minutes because every push to the API except `/plugins` returned `500 cannot extract
  archive` (`/confs`, `/cache`, `/custom_configs`, `/pro_plugins`), so nginx never got its "config received" state. Cause (inferred from
  the code, not from a tar error message, which is discarded): `api.lua` writes the uploaded `.tar.gz` with `io.open(tmp, "w+")`,
  which is text mode on Windows, so each `0x0A` byte of the gzip stream became `0x0D 0x0A` and `tar xzf` failed (tiny archives without a
  `0x0A` byte survive, which is why `/plugins` worked once). Fix written, not yet re-run: `"w+b"` in step 5 of `apply-windows-port.py`.
  If it still fails, the tar error is invisible (os.execute sends stderr to NUL): log it from `api.lua` next. Other `ffi`/`io` text-mode
  traps to watch for: any Lua `io.open(..., "w")` of binary data (`letsencrypt.lua` writes ACME tokens in `w+`, harmless for tokens).
- **Proactive review after the sixth run (unverified):** step 7b of `apply-windows-port.py` patches third-party Lua libraries
  that call libc/winsock via `ffi.C`, which on Windows does not see `ws2_32` or `setenv`: `lua-resty-ipmatcher` (`inet_pton`,
  `ntohl`, used by every IP check), `lua-resty-env` (`setenv`/`unsetenv`/`environ`, called per request by the whitelist plugin),
  `lua-resty-openssl` altname (`inet_pton`/`inet_ntop`), `lua-resty-core` shdict (`free`, defensive). These fail lazily on the
  first request, not at load. The smoke test also has a new check that the busybox applets BunkerWeb shells out to exist.
- **Sixth CI run:** the fifth run's fixes **worked**: nginx now parses the whole configuration and `init_by_lua` runs (modsecurity
  loads, plugin loading starts). `smoke-test` failed in the init phase: `can't load plugin ... antibot : error loading module 'gd'
  from file ...\deps\lib\lua\gd.so: The specified module could not be found`. Cause: `build-native.sh` collected DLLs with
  MSYS2's `ldd`, which only lists *direct* imports, so `libgd.dll` was shipped but not the libpng/libjpeg/freetype/... behind it.
  Fix written, not yet re-run: `copy_dll_closure` (transitive, via `objdump -p` import tables) plus a verify step that fails the
  native build when any shipped binary imports a MinGW DLL that is not in `sbin`. Likely next breakers: later plugins' init, the
  scheduler's first push to the API (`pushswap.lua` moves directories with rename/`cp -a` through busybox), reload via named events.
- **Fifth CI run:** the fourth run's fixes **worked** (generator renders, `default-server-cert` job OK, no cp1252 errors, the
  fail-fast step stopped after ~20 s with the service log tails, WinSW wrapper log wording `Started process <pid>` confirmed).
  `smoke-test` still failed: nginx exited on `unknown directive "http3"` (nginx for Windows has no `http_v3_module`; upstream's
  `HTTP3` defaults to yes), plus the alert `could not open error log file ... usr/share/bunkerweb//bunkerweb/var/log/...` (nginx's
  `-e` only knows drive-letter paths and prepends the prefix to `/bunkerweb/...`; `error_log` in nginx.conf is fine). Fixes
  written, not yet re-run: step 3c of `apply-windows-port.py` (HTTP/3 blocks off, `$http3` in `limitconn.conf` replaced, `HTTP3`
  default `no`, `api.lua` `-e C:/...`) and `-e C:/bunkerweb/var/log/bunkerweb/error.log` in `bwsvc.py`/`bwctl.py`. `Diagnostics` now
  filters the installer log. nginx stops at the first config error, so expect further config/module errors behind this one.
- **Fourth CI run:** everything up to the installer passed; `smoke-test` failed: nginx never became healthy. Cause: the config
  generator died in `Templator` (`ValueError: cannot find context for 'fork'`, so no config, nginx exited and restarted in a loop),
  plus the scheduler's `default-server-cert` job hit `Invalid cache path` (Python 3.14: `Path('/bunkerweb/...').is_absolute()` is
  False) and every log line of generator subprocesses raised cp1252 `UnicodeEncodeError`. Fixes written (steps 12c/12d of
  `apply-windows-port.py`, `_KEEP_ENV` in `sitecustomize.py`), not yet re-run. The `Diagnostics` step was widened (400 lines per
  log, service logs first, generated config listing, installer log).
  The smoke test's "Services are running and nginx is healthy" step now **fails fast**: on every poll it fails when a service is
  Stopped twice in a row, when its WinSW wrapper PID changed, or when `<svc>.wrapper.log` has more than one `Started <pid>` line
  (WinSW restarted it after a crash), and it prints the tail of each service log before throwing. The `Started` pattern for the
  wrapper log (`Started process <pid>`) was confirmed against a real WinSW 2.12 wrapper log in the fifth run.

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
- **Build caches (unverified):** `modsecurity`, `native`, `python` and `package` (the installer) now restore/save their output
  with `actions/cache` (see Pipeline). Not run yet: the first run after this change is all misses, the second one is the first real test of the hit path.
- **Configurable ports (unverified):** `HTTP_PORT`, `HTTPS_PORT`, `API_HTTP_PORT` were already upstream settings; new are
  `HEALTHCHECK_PORT` (global setting injected into `common/settings.json` + `healthcheck.conf` by `apply-windows-port.py`
  step 3b), the web UI port (taken from the port of `UI_HOST`, `bwsvc.py` `ui_env`; `ui.env` `UI_LISTEN_PORT`/`LISTEN_PORT`
  still override) and `bwctl firewall` (the installer's firewall task calls it, so the rule follows `variables.env`).
  The smoke test now picks random free ports (20000-29999) for HTTP, HTTPS, UI, API, health check and the backend and asserts
  firewall rule, listeners and requests against them. Not run yet; watch the settings.json insertion and the new
  "Windows Firewall rule and listeners use the configured ports" step.
- **Next:** push, run the workflow, iterate on failures. Most likely trouble spots, roughly in order: nginx link step
  (OpenSSL whole-archive, libModSecurity import lib, module linking), Python 3.14 wheel availability, `stage.ps1`
  required-files check, installer script, runtime behaviour of the shims and `bwsvc.py` in the smoke test (reload via named
  events, certbot invocation, WinSW service dependencies).
- After a green run (the release `v1.6.17` appears by itself): trial on a non-critical server including a Let's Encrypt *staging* issuance.
- Operator guide and installer have never run on a real server. Do not describe anything as "tested" beyond what the smoke test
  in a green CI run covers.

## Out of scope / do not do

- Do not turn this into a fork of BunkerWeb or vendor modified upstream files into the repo.
- Do not add container, VM or WSL based alternatives: they are explicitly ruled out by the requirements.
- Do not store secrets (tokens, passwords, API keys) in the repo. The operator's `variables.env` with their real hosts and
  backends lives in their own private configuration repo. Examples in the docs use generic names such as `app.example.com`.
