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

1. **Never modify anything in `bunkerweb/`.** It is a pinned git submodule (upstream tag, currently v1.6.18-rc5).
   All Windows changes are applied to a *copy* (`build/src`) at build time by `windows/build/apply-windows-port.py` or come from
   `windows/overlay/`. If upstream code must change, prefer (in this order) a runtime shim in `windows/overlay/`, a pattern in
   `pathrewrite.py`, a patch in `windows/patches/` (via `patchtool.py`, never hand-edited), and only then an anchored one-line
   replacement in `apply-windows-port.py`. `windows/patches/README.md` is the ledger of every source change.
2. **CI is the only test harness.** There is no supported local build and no Windows machine in the dev loop. The pipeline
   runs on `windows-2022`. You cannot verify a change by running it; read logs, reason carefully, and say so when something
   is unverified. The human provides CI logs (`gh run view <id> --log-failed`, or the full `run.log`).
3. **Do not commit, push, tag or create branches unless the user asks.** Releases and their tags are created by the `release` job, not by hand.
4. **Licence is AGPLv3** (upstream). The repo must stay public, or the source must otherwise be available to users of the
   installer. Do not add code under incompatible licences.
5. Keep **forward slashes / POSIX-style absolute paths** where BunkerWeb expects them. Everything lives under the single root
   `C:\bunkerweb\` (mirroring the Linux sub-layout: `usr\`, `etc\`, `var\`). `apply-windows-port.py` (path rewrite in `windows/build/pathrewrite.py`, last step) rewrites
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
  apply-windows-port.py     driver: overlay copy (collision with an upstream file = error), patch series, anchored COUNTED one-line
                            replacements, path rewrite; fails listing every stale patch/anchor
  pathrewrite.py            /etc/bunkerweb -> /bunkerweb/etc/bunkerweb rewrite (import-safe, golden tests in test-port.py)
  patchseries.py            lint + `git apply` (no fuzz) of windows/patches; patchtool.py: import/export/lint, 3-way rebase onto a
                            new upstream tag in a scratch repo (build/patchwork); never writes into bunkerweb/
  port-check.sh             the fast, Linux-friendly checks: patch lint, test-port.py (+ test-winffi.lua through lupa), apply the port,
                            check-port.py (syntax of every changed file), scan-hazards.py (new Linux-isms vs hazards.baseline.txt)
  tools-watch.py            nightly update check of what THIS repo pins (actions `uses:`, WinSW, busybox-w32, waitress, the MSYS2 OpenSSL 3.x package); reads the pins
                            from the workflow/build-python.ps1 itself and fails when it cannot find one; tools-watch.ignore lists declined
                            updates (`<name> <version>  # why`)
  patch-modsecurity.py      same mechanism, but ONLY for what build-modsecurity.ps1 compiles (deps/src/modsecurity); separate so
                            the slow `modsecurity` job's cache key does not depend on the rest of the port
  build-modsecurity.ps1     libModSecurity.dll, MSVC 2022 + conan + CMake (ModSecurity's own build/win32)
  build-native.sh           MSYS2 UCRT64: LuaJIT, nginx.exe with ALL modules static, Lua C modules, Lua libs, runtime DLLs
  build-python.ps1          private CPython copy + hash-pinned deps (--require-hashes --no-deps --target) + certbot DNS alias patch
  prepare-requirements.py   merges BunkerWeb's per-component pip-compile files into one hashed file (drops uvloop)
  stage.ps1                 assembles the tree mirroring C:\bunkerweb\, verifies required files, writes BUILDINFO
windows/installer/bunkerweb.iss   Inno Setup 6 script (services, PATH, firewall, config kept on uninstall)
windows/patches/            git format-patch series (*.patch, .patches order, BASE = upstream commit) + README.md ledger
windows/overlay/            files copied into the build: wincompat.lua + winffi.lua (Lua runtime shims: ffi proxy, io.open,
                            resty.signal, ...), python-site/ (fcntl, pwd, grp, sitecustomize incl. ntpath.isabs), bwsvc.py (service runner), bwctl.py (operator CLI), bwctl.cmd, bwcli.cmd, certbot entry
windows/service/*.xml       WinSW 2.x service definitions: bunkerweb-nginx, -scheduler, -ui, -api
.github/workflows/bunkerweb-windows.yml   pipeline (below)
.github/workflows/port-check.yml          ubuntu, ~1 min: port-check.sh on every push/PR; nightly: `upstream-watch` (port vs. the newest upstream release, opens an `upstream-update` issue) and `tools-watch` (do WinSW, busybox-w32, waitress or an action we pin have a newer release? one `tooling-update` issue, closed when nothing is left)
.github/actions/prepare-src/action.yml    robocopy bunkerweb/src -> build/src, then patch-modsecurity.py + apply-windows-port.py (input `scope: modsecurity` = only the first)
```

## Pipeline (`.github/workflows/bunkerweb-windows.yml`)

`modsecurity` (MSVC/conan, slow, cached) -> `native` (MSYS2/GCC, needs the modsecurity artifact) and `python` (parallel) ->
`package` (stage, Inno Setup, `SHA256SUMS.txt`) -> `smoke-test` (silent install, proxy a request, bwctl checks, UI on :7000,
restart, uninstall) -> `release` (main only; smoke test green: release `v<bunkerweb-version>-win<N>`, e.g. `v1.6.17-win2`, marked latest
(a version with a pre-release suffix, e.g. `v1.6.18-rc5-win1`, is published as a GitHub pre-release and never marked latest);
smoke test red: **draft** `v<bunkerweb-version>-win<N>-RC` with installer and `smoke-test-logs.zip`. Every new revision is a new release,
earlier ones are kept. Only a release/draft with the same tag and draft state (rerun of that revision, retry after a failed smoke
test) is deleted, incl. its tag and assets, and recreated on the built commit; drafts and real releases never replace each other,
but publishing a real release deletes the `-RC` draft of the same revision left by earlier failed attempts).
`<N>` is chosen by the `package` job's `Version` step: the `port_revision` input if given, else the highest published revision of this
bunkerweb version + 1 (the legacy tag `v<version>` counts as 1, drafts are ignored), or the revision of the published release whose
target commit is the current one (a rerun keeps its number).

- **Build caches:** `modsecurity`, `native` and `python` cache their output directory (`build/modsecurity|native|python`)
  and `package` caches the finished installer (`dist/`: setup exe + `SHA256SUMS.txt`) with `actions/cache`. Key = job + `CACHE_VERSION` (workflow `env`) + the commit of the `bunkerweb` submodule
  (`git rev-parse HEAD:bunkerweb`) + `hashFiles(...)` of the repository files that influence that output. On an exact hit the build
  steps (and, for `native`, MSYS2 and the modsecurity artifact download) are skipped, the artifact is still uploaded for the next
  job. **When you add an input to a build script, add it to that job's `hashFiles(...)`**, otherwise a stale cache is served.
  What is *not* in the keys (workflow/MSYS2 package list, runner image, unpinned tools such as conan, pywin32, tzdata): bump
  `CACHE_VERSION`. `native`'s key includes modsecurity's inputs because nginx links its output, plus `pathrewrite.py`, `patchseries.py` and
  `windows/patches/**` (the series changes the nginx/Lua sources it builds); `modsecurity`'s key hashes
  `patch-modsecurity.py`, **not** `apply-windows-port.py` (its job runs `prepare-src` with `scope: modsecurity`): a patch that changes
  what `build-modsecurity.ps1` compiles must go into `patch-modsecurity.py`, everything else into `apply-windows-port.py`;
  `python`'s key contains the exact
  CPython version from `setup-python`. Cache entries are scoped per branch, unused entries
  expire after 7 days. `smoke-test` is never cached.
    `package`'s key = `CACHE_VERSION` + version + port revision + bunkerweb commit + CPython version + `BUSYBOX_SHA256` +
    `WINSW_SHA256` + `hashFiles('windows/**', '.github/actions/**')`. On a hit, prepare-src, both artifact downloads, the tool
    download, stage and ISCC are skipped and the restored `dist/` is uploaded as the `installer` artifact (the `stage` artifact
    does not exist then). The `Version` step still runs, so the `release` job is unaffected. The revision is the `port_revision`
    input of a manual run, else the next free one (see above). **The workflow file itself is not in any key:** changing the package,
    stage or ISCC steps or the tool download logic needs a `CACHE_VERSION` bump; editing only smoke-test steps keeps the hit.
  The cache is saved by an explicit `actions/cache/save` step right after a successful build (the restore is
  `actions/cache/restore`), so a failing artifact upload or failing later jobs do not lose it. The conan packages of the
  ModSecurity build are saved even if that build fails (a partial conan cache is kept for that key until the
  `conanfile.txt` changes).

- Every job: `core.autocrlf false` + `core.longpaths true`, checkout with submodules, setup-python, then `prepare-src`.
  LF endings matter: the anchored replacements match exact text including newlines.
- Downloads are pinned and SHA-256 verified (busybox-w32, WinSW 2.13.0 `WinSW.NET462.exe_Release`, and the MSYS2 OpenSSL 3.x package
  `MSYS2_OPENSSL_PKG`, installed with `pacman -U` because MSYS2's default is OpenSSL 4 since Nov 2026). Change URL/name and hash together.
  The nightly `tools-watch` job (`port-check.yml`) reports newer releases of these, of waitress and of every action; an update that
  was looked at and declined goes into `windows/build/tools-watch.ignore` (none at the moment). If you rename `WINSW_URL`, `BUSYBOX_URL`,
  `MSYS2_OPENSSL_PKG` or `$WAITRESS_VERSION`, `tools-watch.py` must follow (a unit test fails otherwise). The `native` cache key contains
  `MSYS2_OPENSSL_SHA256`.
- `PYTHON_VERSION` is `3.14` (what BunkerWeb's Linux packages use). If a dependency has no cp314 wheel, switch to `3.13`.
- Release/tag format: `v<VERSION>-win<N>` (release) and `v<VERSION>-win<N>-RC` (draft); `<VERSION>` is `bunkerweb/src/VERSION` of the submodule
  with `~` replaced by `-` (`1.6.18~rc5` -> `1.6.18-rc5`; git refs cannot contain `~`, and Inno Setup's `VersionInfoVersion` gets only
  the numeric part, `AppNumericVersion`), `<N>` the port revision, also in the installer file name (`-win<N>`). Revisions accumulate,
  they are not overwritten.
- Artifacts: `modsecurity`, `native`, `python`, `stage`, `installer`, `smoke-test-logs` (first place to look after a failure).

## How to work on a change

- **Before pushing**: `bash windows/build/port-check.sh` (`pip install lupa jinja2`, `BW_REQUIRE_LUPA=1`) runs everything that does not
  need Windows. It is also what `port-check.yml` runs.
- **A build step fails**: find the first real error in the log (`##[error]` is only the exit code; the cause is above it).
  Fix in `windows/`, never in `bunkerweb/`. Typical fixes: a shim in `windows/overlay/` (preferred), a patch (`patchtool.py import`,
  edit and commit in `build/patchwork`, `export`), one more anchored replacement in `apply-windows-port.py`
  (`rep(file, old, new, count)` fails the build if the occurrence count differs, which is intended), or a compiler/linker flag in
  `build-native.sh`.
- **A new hazard in the scan** (`scan-hazards.py`): review, shim/patch if needed, then `scan-hazards.py build/src --update` and commit the baseline.
- **Upgrading BunkerWeb**: follow "Upgrading BunkerWeb" in `windows/README.md` and the checklist in "Versions, pins and updates" below
  (`patchtool.py import --rev <tag>` moves the patches with a 3-way merge). Expect stale patches/anchors, new nginx modules or Lua
  libraries in `src/deps/deps.json`, and Python requirements without Windows wheels.
- **Bumping a pinned tool or action** (WinSW, busybox, OpenSSL, waitress, an action): see "Versions, pins and updates" below.
- **Runtime behaviour** (services, reload, certbot, shims) can only be exercised by the smoke test. When you add behaviour,
  add a check to the smoke test if it is cheap. When you cannot test, document the risk.
- Keep `README.md` consistent with reality: ports, paths, service names, `bwctl` commands, defaults. The operator guide is
  part of the deliverable.
- Settings defaults quoted in the guide were checked against the 1.6.18-rc5 sources (`LETS_ENCRYPT_CHALLENGE=http`,
  `AUTO_REDIRECT_HTTP_TO_HTTPS=yes`, `DISABLE_DEFAULT_SERVER=no`, `USE_BACKUP=yes`, `USE_MODSECURITY_GLOBAL_CRS=no`,
  `USE_BUNKERNET`/`USE_DNSBL=yes`, `USE_ZSTD=no`). Re-verify after upgrades.

## Versions, pins and updates: what to change where, what to validate

Two kinds of versions exist. **BunkerWeb's own** (submodule tag): everything BunkerWeb builds from (nginx, LuaJIT, lua-* libraries,
ModSecurity, zstd, ... from `src/deps/deps.json`; Python packages from its lock files) moves with it. **Ours**: what this repository
pins itself (below). `tools-watch` watches ours, `upstream-watch` watches BunkerWeb; both are jobs of `port-check.yml` (nightly
04:23 UTC + manual) and each keeps one GitHub issue (`tooling-update` / `upstream-update`). Never add a third workflow for this.

### Pins of this repository

| Component | Pinned in | Hash / integrity | Cache effect | Watched by `tools-watch` as | Also check when you change it |
|---|---|---|---|---|---|
| BunkerWeb | submodule `bunkerweb/` (tag) | git | all keys contain `git rev-parse HEAD:bunkerweb` | `upstream-watch` (not tools-watch) | the whole upgrade checklist below |
| GitHub Actions | `uses: owner/repo@vN` in `.github/workflows/*.yml` and `.github/actions/*/action.yml` | major tag | none | every `uses:`; a new *major* is reported (`@v7` follows v7.x itself), an exact pin (`@v7.0.1`) any newer release | the release notes: Node runtime, renamed or removed inputs, changed defaults (e.g. `upload-artifact` skips hidden files, see Gotchas); cache actions: key semantics |
| WinSW | `WINSW_URL` + `WINSW_SHA256` in the workflow `env` | SHA-256 | `package` key contains `WINSW_SHA256` | `winsw` | `windows/service/*.xml` (comments say "WinSW 2.x"), the smoke test's service checks: its fail-fast relies on the wrapper log wording `Started process <pid>` and the `roll-by-size` log mode; the asset name changes between releases (`WinSW.NET462.exe_Release` in 2.13) |
| busybox-w32 | `BUSYBOX_URL` + `BUSYBOX_SHA256`, fallback `BUSYBOX_FALLBACK_URL`/`_MEMBER` (Chocolatey package of the same release; **the binary must have the same SHA-256**) | SHA-256 | `package` key contains `BUSYBOX_SHA256` | `busybox-w32` (frippery.org, GitHub tags as fallback) | the applets BunkerWeb shells out to (smoke test "busybox applets" step); the fallback version number must follow the main one |
| OpenSSL (MSYS2 package, linked statically into `nginx.exe`) | `MSYS2_OPENSSL_PKG` + `MSYS2_OPENSSL_SHA256` in the workflow `env`, installed by the "Pin OpenSSL" step with `pacman -U` | SHA-256 | `native` key contains `MSYS2_OPENSSL_SHA256` | `msys2-openssl` (newest package of the **same major** in the MSYS2 archive; a new major is never reported) | `build-native.sh` "OpenSSL version" group (fails unless `openssl version` is 3.x) and the `libssl-3*.dll` globs, patch 9 (Templator probe loads `libssl-3*.dll`), `lua-resty-openssl`'s version table. A move to 4.x is a project decision, not a bump |
| waitress (web UI server) | `$WAITRESS_VERSION` in `windows/build/build-python.ps1` | PyPI (no hash) | `python` key hashes that file | `waitress` | `bwsvc.py` UI start-up (`ui_env`), the smoke test's UI step |
| CPython | `PYTHON_VERSION` in **both** workflows' `env` (`bunkerweb-windows.yml`, `port-check.yml`) | none | `python` key contains the exact version | not watched (follows BunkerWeb's Linux packages, currently 3.14) | every dependency needs a cp3xx wheel for Windows; `sitecustomize.py` shims (`ntpath.isabs` is only needed from 3.13); `build-python.ps1` |
| conan | `conan>=2.27.1,<3` in the `modsecurity` job | none | conan packages cached by the hash of ModSecurity's `conanfile.txt` | not watched (floats inside the range) | ModSecurity's own `build/win32` expects it |
| `CACHE_VERSION` | workflow `env` | - | part of every key | - | see "Cache rules" |

Floating on purpose (unpinned, **not** watched): all other MSYS2 packages (gcc, binutils, pcre2, zlib, libgd, libmaxminddb, file, sqlite3),
Inno Setup (preinstalled on the runner, else `choco`), MSVC / conan toolchain, `pywin32`/`colorama`/`tzdata`/`pip`, the `windows-2022`
image, `lupa`/`jinja2` for the checks. They change under you without a commit: when a build that worked yesterday fails in
unchanged code, suspect them first (the OpenSSL 4 switch of MSYS2 in Nov 2026 is the example), read the versions in the log
(`BUILDINFO`, MSYS2 install step) and either fix forward or pin like OpenSSL (package file from `https://repo.msys2.org/mingw/ucrt64/`,
which keeps old versions, installed with `pacman -U`, SHA-256 verified).

### Rules for every pin

1. **URL/file name and hash change together.** Compute the hash from the real download (`sha256sum`), and compare it with the digest
   GitHub shows for a release asset when there is one. Never copy a hash from an issue or a guess.
2. Read the release notes between the old and the new version for what *this* repo uses (see the table's last column) before bumping.
3. Keep `tools-watch.py` in step: it reads the pins with regexes (`find_pins`) from the workflow / `build-python.ps1` and exits 2 when
   it cannot find one. If you rename `WINSW_URL`, `BUSYBOX_URL`, `MSYS2_OPENSSL_PKG` or `$WAITRESS_VERSION`, change the regex too
   (the unit test `ToolsWatch.test_every_pin_is_found` fails otherwise). A **new** pin needs: its regex in `find_pins`, a lookup in
   `check()`, a unit-test assertion, a line in the docstring, `tools-watch.ignore` name list, and the mentions in `port-check.yml`'s
   header, `windows/README.md` ("Fast checks") and this file.
4. A version you looked at and declined goes into `windows/build/tools-watch.ignore` (`<name> <version>  # why`). It silences that exact
   version only; the issue closes itself when nothing is left. Do not edit the issue by hand.
5. Exit codes of `tools-watch.py`: 0 current, 10 updates (issue created/edited), 11 a lookup failed and nothing else was found (issue left
   alone), 2 a pin is missing (job fails: the script is out of date).
6. Tell the user in the same message what changed in the pipeline and that it is unverified until a CI run proves it.

### Cache rules (silent staleness is the failure mode)

- Keys: see "Build caches" above. The workflow file itself is in **no** key, so: changing *what a job builds or how* (package list,
  tool download, stage/ISCC invocation, a runner assumption) needs a `CACHE_VERSION` bump; editing smoke-test steps does not.
- A new input file of a build script needs adding to that job's `hashFiles(...)`; a new pinned download needs its hash in the key
  (like `MSYS2_OPENSSL_SHA256`, `WINSW_SHA256`, `BUSYBOX_SHA256`).
- What changes `modsecurity`'s output goes through `patch-modsecurity.py`, everything else through `apply-windows-port.py`: the first
  one is in `modsecurity`'s key, the second is not.
- After a bump, the first run is all cache misses (the `modsecurity` job alone takes ~20 min with a cold conan cache).

### Checklist: upgrading BunkerWeb (submodule tag)

1. Follow "Upgrading BunkerWeb" in `windows/README.md` (`git -C bunkerweb checkout <tag>`, `patchtool.py import --rev <tag>`, resolve
   conflicts, `export`, `port-check.sh`). The `upstream-update` issue shows the failures of the newest release in advance.
2. Diff upstream for what no check sees (`git -C bunkerweb diff --stat <old> <new> -- <paths>`, then read the real diffs):
   `src/deps/deps.json` (new/updated nginx modules, Lua libraries, libraries to build: `build-native.sh`, filter order),
   `src/linux/scripts/*.sh` + `src/common/helpers/utils.sh` (ported in `bwsvc.py`), `src/*/requirements*.in/.txt` (wheels for
   Windows; `prepare-requirements.py`), `src/common/settings.json` + `src/common/core/*/plugin.json` (new settings, changed defaults:
   re-verify the ones quoted in README/AGENTS), `src/common/confs/*` and `nginx.conf` (new directives nginx for Windows lacks:
   `http3`, `reuseport`, `load_module` order), new `ffi.C`/`os.*`/`io.*` use in Lua (the hazard scan lists them), `fork`/signals/
   `multiprocessing` in Python, new paths outside `/etc|/usr|/var/.../bunkerweb`.
3. `src/VERSION` format: `1.6.18~rc5` becomes tag `v1.6.18-rc5-win<N>`; a suffix makes it a GitHub pre-release. Check the `Version`
   step still parses it and ISCC gets the numeric part.
4. Refresh the hazard baseline (`scan-hazards.py build/src --update`) only after reviewing every new line.
5. Update: `windows/patches/README.md` (ledger, "Check:" lines), `windows/README.md` (mapping table, upgrade notes), `README.md` (version,
   quoted defaults, new settings operators need), this file's "Current state" (what was done, what is unverified, what to watch in the
   next run) and the settings-defaults bullet above. Bump `CACHE_VERSION` only if a build-script change needs it (the submodule
   commit already invalidates the BunkerWeb-dependent keys).
6. Validate: `port-check.sh` green; then CI in job order. Read the **first real error** in each job; the unverified list in
   "Current state" tells what has never run. After a green smoke test the pre-release appears by itself.

### Worked example: v1.6.17 -> v1.6.18-rc5 (what it taught)

The upgrade is documented step by step in "Current state"; the lessons that apply to the next one:

- **The Linux port check going green says little about the build.** The patch series moved with one conflict (the Templator patch),
  and `port-check.sh` passed, yet CI then failed three times in the `native` job, each time on something no Linux check covers:
  (1) the new nginx module `zstd-nginx-module` leaked `NGX_STREAM_LUA_HAVE_SA_RESTART` into stream-lua because nginx sources all module
  `config` scripts in one shell (patch 11 + a "configure sanity" step); (2) MSYS2's floating `openssl` package became 4.1 in the middle of
  the upgrade, which had nothing to do with BunkerWeb (now pinned to 3.x, see the table); (3) whatever comes after in the log.
  Budget for several CI iterations and read the log job by job.
- **Start with the dependency diff, not with the patches.** `git -C bunkerweb diff --stat v1.6.17 v1.6.18-rc5 -- src/deps/deps.json`
  showed zstd (library + nginx module), which is where most of the work was: `build-native.sh` (static `libzstd.a`, `--add-module`,
  link flags, export check, BUILDINFO), a smoke-test site with `USE_ZSTD=yes`, and the filter-order assertion (static modules ignore
  `load_module` order). A **new static nginx module** needs: its build in `build-native.sh`, a check of its `config` script for shell
  state it relies on or leaks, an entry in the filter-order assertion if it is a filter, and a smoke-test hit.
- **Read the upstream diff of the files we mirror**, not just what failed: `src/linux/scripts/*.sh` and `utils.sh` changed (`USE_IPV6`,
  env-file keys that change execution, config fallback on a failing start, alembic handling) and had to be ported into `bwsvc.py` by hand;
  new Lua (`enforcement_state.lua`, CrowdSec) used `ffi.C.open/write/close/getpid` and `os.rename` over an existing file, so `winffi.lua`
  and `wincompat.lua` got shims (with unit tests in `test-winffi.lua`). New Python requirements (`certbot-dns-hosttech`, `lazy-importer`)
  were pure wheels; check each new one for a Windows wheel and for data files that `pip --target` drops.
- **New template variables break blunt text edits.** Upstream turned the stream `reuseport` into a Jinja variable; the old
  "remove ` reuseport`" replacement cut the variable name. After every upgrade look at the anchored replacements' *results*
  (`build/src`), and let `check-port.py` encode the invariant (it now ignores Jinja identifiers).
- **Pre-release tags need the pipeline to cope**: `src/VERSION` is `1.6.18~rc5`; `~` becomes `-` in tags and file names, Inno Setup gets only
  the numeric part, the release is a GitHub pre-release and never `latest`, and `upstream-watch` must not open an issue against 1.6.17
  just because the newest *regular* release is older than the pinned rc. When the final `v1.6.18` is released, upgrade again from the rc
  (the diff should be small) and expect the release name to lose its `-rc5`.
- **Re-verify quoted facts**: the defaults in README/AGENTS (settings list above) were re-checked against the new sources, and the mapping
  table, ledger `Check:` lines and hazard baseline were refreshed. `CACHE_VERSION` was bumped because the ISCC invocation changed, not
  because of the submodule (its commit is already in the keys).
- **Reproduce on Linux whenever the cause is source-level.** The `SA_RESTART` leak was proven by running nginx's real `configure` against
  the vendored sources with a forced "not found"; that is cheap and beats guessing. Use `patch -p1` in a scratch dir inside the repo
  (`git apply` resolves paths against the repo root there).

### Checklist: bumping a pinned tool

1. Read the table row above; check the release notes for the "also check" items.
2. Change version/URL and hash together (workflow `env` or `build-python.ps1`); keep comments next to the pin accurate.
3. Does the pin sit in a cache key by hash/version (WinSW, busybox, OpenSSL, waitress, CPython: yes)? Then no bump. Otherwise bump `CACHE_VERSION`.
4. `bash windows/build/port-check.sh` (it unit-tests the pin parsing; run `python windows/build/tools-watch.py` too when the network
   is available: it must print the new version as current).
5. Update the docs that quote it (`AGENTS.md` Pipeline + Current state, `windows/README.md`, workflow header comments).
6. Say what to watch in the next CI run (the table's last column is the list).

### Checklist: a GitHub Action major

Bump every occurrence (`grep -rn 'uses: owner/repo@' .github`), including `.github/actions/*/action.yml`; read the release notes
(inputs, Node version, behaviour changes); keep the `include-hidden-files: true` on artifacts that carry dotfiles; remember that
`actions/cache` restore/save versions are used as a pair; add a note in "Current state".

### What "validated" means here

- Validated on Linux: `port-check.sh` (unit tests incl. pin parsing, patch lint, port applied, syntax of changed files, hazard scan).
  That proves the port still applies, **not** that anything builds or runs.
- Validated by CI only: compile/link, DLL closure, installer, services, the smoke test. Until a run is green, write "unverified" and
  list the specific risks (what could fail and where to look), as the entries in "Current state" do. Never write "tested" for operator-facing
  behaviour beyond what the smoke test covers.
- Always finish an update task with: what changed, what the Linux checks said, what only CI can tell, and the next thing to look at in the log.

## Gotchas learned so far

- **Windows-only Python deps are missing from the Linux lock files** (`pywin32` for certbot, `colorama`, `tzdata` for psycopg/zoneinfo).
  The hashed install
  therefore uses `--no-deps` and a second unhashed step installs them; `pip check` reports other gaps. pywin32 lives in a
  `--target` dir, so `sitecustomize.py` processes its `.pth` file explicitly.
- **nginx for Windows does not define `NGX_HAVE_VARIADIC_MACROS`**; code relying on `dd(...)`-style macros compiles arguments that
  only exist in debug builds (fixed for modsecurity-nginx by a patch of `windows/patches`). Expect similar breakage in other
  third-party nginx modules.
- nginx's `configure` skips system OpenSSL/PCRE2/zlib on win32; the port patches that (`windows/patches`). OpenSSL is linked whole-archive
  into `nginx.exe` so LuaJIT FFI (`ffi.C`) finds `SSL_*`/`EVP_*`.
- nginx for Windows: one worker, `select()` event model (`FD_SETSIZE=4096` at build time), no reuseport, no HTTP/3, signals via
  named events `Global\ngx_<signal>_<pid>`, files opened with `FILE_SHARE_DELETE`.
- libModSecurity is built with MSVC (its supported Windows toolchain), nginx with MinGW/UCRT; they meet through the plain
  C API (`msc_*`) and a generated import library.
- **`ffi.C` on Windows only sees the exe exports, lua51.dll, the CRT, kernel32/user32/gdi32**: winsock (`inet_pton`, `ntohl`) needs
  `ffi.load("ws2_32")`, and `setenv`/`unsetenv`/`environ` do not exist in the UCRT (`_putenv_s`, `__p__environ`). Such calls load fine
  and fail on the first request. `winffi.lua` (installed as `package.loaded["ffi"]` by `wincompat.lua`) now resolves them for every
  module required after it (real `ffi.C` -> ws2_32 -> ucrtbase, plus overrides); the scan-hazards `ffi.C symbol`/`ffi.load` entries
  list what upstream uses. Modules loaded *before* `init_by_lua` (resty.core) cannot be reached: that is why the shdict `free()` is a patch.
- The submodule contains a few symlinks (mbedtls examples, brotli research); harmless on Windows.
- **`actions/upload-artifact` (v4.4+) skips hidden files and directories (names starting with `.`) unless `include-hidden-files: true`.**
  The build artifacts hand dotfiles from job to job (`certbot_dns_transip\.VERSION` was silently dropped between `python` and `package`, which
  is why the plugin was missing in the first installers); every artifact that feeds a later job sets the option. `actions/cache` keeps them.
- **`pip --target` drops `data_files`**: certbot-dns-transip reads `.VERSION` (a `setup.py` data file) and logged "plugin errored while loading" in
  every certbot run; `build-python.ps1` recreates `deps\python\certbot_dns_transip\.VERSION` from the dist-info and imports the plugin in the
  import check (unverified in CI). Other plugins that read data files next to their package would need the same.
- Let's Encrypt data lives in `C:\bunkerweb\var\cache\bunkerweb\letsencrypt` (`/var/cache/bunkerweb/letsencrypt` upstream).
- Upstream UI/API use gunicorn; the port uses waitress (UI) and uvicorn without uvloop (API).
- **Python >= 3.13: a rooted path without drive (`/bunkerweb/...`, what the path rewrite produces) is not absolute on Windows**
  (`Path.is_absolute()`/`os.path.isabs()` are False). `sitecustomize.py` restores the pre-3.13 `ntpath.isabs` (unit-tested on Linux against
  `PureWindowsPath`); the certbot entry point undoes it. No upstream validator is patched any more.
- **No `fork` on Windows:** the config generator (`Templator.render`) forced the fork start method and used a
  `ProcessPoolExecutor` (since 1.6.18 with an explicit `mp.get_context("fork")`); a patch renders in one worker thread instead.
  Any other `multiprocessing` use needs the same care.
- **Static nginx modules ignore `load_module` order.** Upstream fixes filter order (e.g. ModSecurity before Brotli in 1.6.18) by
  reordering `load_module` in `nginx.conf`, which the port deletes; the static order comes from each module's `config` script.
  `build-native.sh` asserts `gzip < brotli < zstd < modsecurity < postpone` in `objs/ngx_modules.c` (checked on Linux with a
  real `configure` run: that order already held for 1.6.17).
- **`os.rename` in Lua does not replace on Windows** (the CRT's `rename()` refuses an existing target): `wincompat.lua` maps it to
  `MoveFileEx(MOVEFILE_REPLACE_EXISTING)`. `ffi.C.open` with Linux `O_*` values would be wrong, not just missing (Linux
  `O_CREAT` 0x40 is `_O_TEMPORARY` in the UCRT): `winffi.lua` translates them.
- Child Python processes spawned by BunkerWeb with an explicit `env=` lose `PYTHONUTF8`/`PYTHONIOENCODING` unless
  `sitecustomize.py` re-adds them (`_KEEP_ENV`); without them emoji log lines fail with cp1252 `UnicodeEncodeError`.
- The smoke-test job's `Diagnostics` step prints all service/BunkerWeb logs into the run log (grouped per file).

## Current state

*Last updated 2026-11-20 (upgrade to 1.6.18-rc5). Update this section whenever the pipeline state changes. Step numbers (3c, 7b, 12b, 12c, 12d, 13, ...) in the
history below refer to `apply-windows-port.py` as it was before the robustness refactor (the multi-line ones are now patches, see the ledger in
`windows/patches/README.md`).*

- **Native job of the second 1.6.18-rc5 run (2026-11-20, fix unverified):** nginx now compiles and links (zstd static, patch 11 worked).
  It failed in "runtime DLLs and tools" with `libssl-3*.dll is missing from sbin`: the MSYS2 `openssl` package had moved to **4.1.0**
  (DLLs `libssl-4-x64.dll`), so OpenSSL 4 had been linked into `nginx.exe` as well. Decision: stay on 3.x, as BunkerWeb's own images do
  (3.5); nothing has ever run on 4 (nginx, lua-nginx-module, lua-resty-openssl's FFI declarations, the Templator `libssl` probe). The
  workflow pins `MSYS2_OPENSSL_PKG` 3.6.5-1 from the MSYS2 archive (`pacman -U` after the normal install, SHA-256 checked, part of the
  `native` cache key), `build-native.sh` has an "OpenSSL version" group that fails first thing when it is not 3.x, and `tools-watch.py`
  reports newer 3.x packages (never 4.x). Unverified: `pacman -U` downgrade over the installed 4.1 (a package that depends on 4 would
  stop it), the rest of the native job (verify group, DLL closure) and everything after it.

- **Native job of the first 1.6.18-rc5 run (2026-11-20, fix unverified):** failed in `nginx: build` with
  `'NGX_RECONFIGURE_SIGNAL' undeclared` / `struct sigaction` unknown in `stream-lua-nginx-module/.../ngx_stream_lua_util.c`. Everything
  before it passed (zstd compiled, `configure`, the filter-order assertion). Cause: **our zstd addition**, not upstream. nginx sources
  all module `config` scripts in one shell and `auto/feature` defines `$ngx_feature_name` when a test passes; `zstd-nginx-module`
  never sets it, so it inherited stream-lua's `NGX_STREAM_LUA_HAVE_SA_RESTART` and stream-lua compiled its `sigaction` code (the
  configure log said `SA_RESTART ... not found` for both lua modules). Reproduced on Linux with nginx's real `configure` (leak with
  a forced "not found", clean with the patch). Fix: patch 11 (`zstd-nginx-module-do-not-inherit-...`), plus a "configure sanity"
  step in `build-native.sh` that fails right after `configure` if either `*_LUA_HAVE_SA_RESTART` is defined. The zstd link and the
  rest of `make` are still unseen. Lesson: a new static nginx module must be checked for config scripts that rely on shell state.
- **Tooling watch (2026-11-20, unverified in CI):** `port-check.yml` got a third job, `tools-watch` (nightly + manual, no separate
  workflow): `windows/build/tools-watch.py` finds the pins itself and looks up the newest release (GitHub releases, frippery.org with the
  GitHub tags as fallback, PyPI). One issue labelled `tooling-update` is created/edited, and closed once nothing is left; an
  inconclusive lookup (exit 11) leaves it alone. Checked by hand the same day: all actions are on their newest major, busybox
  FRP-6354 and waitress 3.0.2 are current.
- **WinSW 2.12.0 -> 2.13.0 (2026-11-20, unverified in CI):** upstream calls the release "largely experimental" (not on NuGet), which was
  the only reason to hold back; the diff shows no change to what we use: the `Started process <pid>` wrapper log wording and the
  `roll-by-size` log mode are untouched (the log change only replaces the zip library), `stopparentprocessfirst`'s process-tree stop
  now keeps a handle per child (PID reuse), `SignalStopped` no longer throws. The .NET Framework build is now `WinSW.NET462.exe`
  (4.6.1 dropped; Windows Server 2019 has 4.7.2). The asset is `WinSW.NET462.exe_Release`; SHA-256 computed from the download and
  equal to the digest GitHub shows. The `package` cache key contains `WINSW_SHA256`, so no `CACHE_VERSION` bump. Watch in the next run:
  the smoke test's stop/restart/uninstall steps (the service stop path) and the `Started process` count in `<svc>.wrapper.log`.
- **Upgrade v1.6.17 -> v1.6.18-rc5 (2026-11-20, Linux port check green, nothing of it has run in CI):**
  - Patch series: `patchtool.py import --rev v1.6.18-rc5`; only the Templator patch conflicted (upstream now renders the
    global templates first and passes `mp.get_context("fork")` explicitly), resolved keeping the single worker thread, `Check:`
    line updated. The other nine rebased cleanly.
  - `apply-windows-port.py`: stream listeners now take `reuseport` from a Jinja variable (`reuseport_directive`, set for the first
    service on a shared port); the blunt `" reuseport"` removal cut the variable name (`TemplateSyntaxError`), so the stream template
    now blanks the variable's value. `check-port.py`'s reuseport invariant ignores Jinja identifiers.
  - New upstream deps: `zstd` + `zstd-nginx-module` (`USE_ZSTD`, off by default): `build-native.sh` builds `libzstd.a` from the
    vendored source (MinGW, asm included) and links the module statically (`ZSTD_INC`/`ZSTD_LIB`, like upstream's `install.sh`);
    unverified on MinGW. The vendored `yajl` is only for RHEL 10; ModSecurity on Windows already gets yajl from conan, so
    `MODSECURITY_SEC_AUDIT_LOG_FORMAT=JSON` should work (conan's yajl 2.1.0 lacks the Debian CVE patches upstream applies).
  - New Lua hazard: CrowdSec `enforcement_state.lua` writes checkpoints with `ffi.C.open/write/close/getpid` (Linux flag
    `524481`) and `os.rename` over the previous file. Shimmed in `winffi.lua` (UCRT `_open/...`, flags translated, unit-tested)
    and `wincompat.lua` (`os.rename` -> `MoveFileExA` with replace; untested). Only matters when the CrowdSec plugin is used.
  - `bwsvc.py` mirrors the 1.6.18 Linux scripts: `USE_IPV6` in the temporary config, env-file keys that change execution are
    ignored (`is_execution_env_key` + Windows names), a preserved config (`KEEP_CONFIG_ON_RESTART=yes`) that fails to start falls
    back once to the loading config and drops `pushswap/*.applied`, alembic runs on a private `alembic.ini` copy (`ALEMBIC_CONFIG`,
    `-s`, `stamp --purge`, abort when stamping fails; the installed `alembic.ini` was edited in place before).
  - Python: new `certbot-dns-hosttech` and `lazy-importer` (pure `py3-none-any` wheels, no data files); smoke test now requires
    `dns-hosttech` in `certbot plugins` and enables `USE_ZSTD` for its test site.
  - `src/VERSION` is `1.6.18~rc5`: the `Version` step writes `~` as `-` (tag `v1.6.18-rc5-win<N>`), passes the numeric
    `AppNumericVersion` to ISCC, and such versions are released as GitHub pre-releases (not latest). `port-check.yml`'s
    upstream-watch normalises `~` too and checks the pinned version when the latest regular release is older (no false issue
    against 1.6.17). `CACHE_VERSION` bumped to 2 (ISCC invocation changed).
  - Reviewed and judged harmless on Windows: `bwcli support`'s `systemctl` probe (returns "" on `OSError`), `api.lua`'s new reload
    rejection probe via `/proc` (guarded by `ffi.os ~= "Linux"`), `ssl_client_hello_by_lua_block` (MSYS2 OpenSSL 3 has the
    callback), the `#bw-env-format:2` encoding (only generated files carry it; `bwsvc.py`/`bwctl.py` read the operator's file),
    `Templator._write_config`'s `tmp.replace()` (failure is logged and the generation retried).
  - Watch in the next CI run: zstd/asm build under MinGW, the filter-order assertion, `ngx_http_lua_ffi_ssl_get_client_hello_ext`
    export, the reload path (reworked upstream: `wait_reload` confirms through `misc_ready_<token>` in the internal store), the
    scheduler's parallel job runner, and the first migration from a 1.6.17 database (none in CI: fresh install).

- **Field report 2026-11-14 (first real install, unverified fixes):** Let's Encrypt never issued because certbot aborted while loading
  `certbot_dns_transip` (missing `.VERSION`; `build-python.ps1` now recreates it, `stage.ps1` requires it, the smoke test runs
  `certbot plugins`). The default `variables.env` no longer contains `IS_LOADING=yes` (the nginx component's temporary config sets it
  itself); existing `variables.env` files are never rewritten, so installs from earlier builds keep the line until removed by hand.
- **Field-report log analysis (same install, `tmp/bunkerweb-logs`, unverified fixes):**
  (a) `IS_LOADING=yes` in `variables.env` kept `/healthz` at `loading`: 186 reloads / 456 workers in 90 min, ended when the line was removed (already fixed above).
  (b) `Folder cache entry is outside the cache roots: \bunkerweb\var\tmp\bunkerweb\failover` (28x), which also failed `download-crs-plugins`,
  MMDB and update-check cache restores: `cache_restore.checked_folder_target` compares against `Path.resolve()`d roots; on Windows `resolve()`
  adds `C:`, and `_check_path` only maps the cache/config roots, not tmp. Patch 10 (`cache-restore-accept-the-installed-tmp-root-for-folder`);
  the smoke test greps `scheduler.log` for the message.
  (c) `CERTIFICATE_VERIFY_FAILED` in urllib (MMDB jobs; `requests`/certifi jobs were fine): cause **inferred** (Windows root store of the server
  lacks the CA), `base_env()` in `bwsvc.py` now sets `SSL_CERT_FILE` to certifi's bundle (additive to the Windows store; `sitecustomize` keeps it for
  children with an explicit `env=`); `stage.ps1` requires `certifi\cacert.pem`. If it still fails, the CA is not in certifi either (TLS inspection?).
  (d) the temporary start config had no `WORKER_PROCESSES`, so the first start spawned one worker per CPU thread: `TMP_ENV_DEFAULTS` now sets 1.
  Not port bugs (documented in README 12.3): Let's Encrypt HTTP-01 timeouts with no request in `access.log` (public port 80 not forwarded),
  Spamhaus DNSBL refusing public resolvers.

- **Field report 2026-11-15 (unverified fix):** a service named `nas.<domain>` made every config test fail with `CreateFile() "C:/bunkerweb/etc/nginx<LF>as.<domain>/set-lua.conf"
 failed (123)`. `Templator._get_server_config` builds `NGINX_PREFIX` with `os.path.join`, which on Windows yields `/bunkerweb/etc/nginx\nas...`, and nginx's
  parser turns `\n`, `\t`, `\r` into control characters (so only server names starting with n/t/r broke; others got a harmless backslash). Fixed by a
  one-line replacement in `apply-windows-port.py` (`Path(...).as_posix()`). Watch for other `os.path.join` results that end up in generated nginx config.
  The `/api/session/properties` 404s in the same log are internet scanners, not a bug.

- **Tenth CI run (first with the refactor + ninth-run fixes):** failed in "bwctl status / test / reload": `bwctl reload` got `503 a reload is in
  progress (.../reloading exists)` from upstream's reload serialisation (one reload at a time; the new workers remove the marker from a
  timer ~5 s after start, the API waits only 4 s). Cause **not proven**: most likely the scheduler's own reload shortly after start
  (it retries such a 503, `bwctl` did not); a marker that is never removed (worker-side cleanup on Windows) is the alternative. Fix written,
  not yet re-run: `bwctl reload` retries a 503 for up to 90 s and prints the marker's age on a persistent failure; the `Diagnostics` step
  lists `var\tmp\bunkerweb` (marker content and mtime). If the next run still fails, the marker is stale: check the `init_worker` timer
  (`release_reload_marker` in `init-worker-lua.conf`) and the token comparison on Windows.
- **Robustness refactor (nothing of it has run in CI):** the port was restructured so that upstream
  releases break less (layers: runtime shims > path patterns > patch series > one-line anchors, see `windows/patches/README.md`).
  What changed:
  - 7 multi-line edits (9 with the ninth-run fixes) moved from `apply-windows-port.py` into `windows/patches/*.patch` (git format-patch files, applied with
    `git apply`, moved to new upstream tags with `patchtool.py import --rev`); `windows/build/pathrewrite.py` holds the path rewrite.
  - Per-call-site patches replaced by runtime shims: **`winffi.lua`** (an `ffi` proxy that `wincompat.lua` installs as
    `package.loaded["ffi"]`; replaces the `api.lua`/`stream_lock.lua` `C_` edits, the iputils/ipmatcher/altname/env patches),
    `wincompat.lua` additions (binary `io.open` for writing modes replaces the `"w+b"` edit, `/proc/uptime` in `io.open`,
    `package.preload["resty.signal"]`, `ngx.process.get_master_pid` override) and `sitecustomize.py` (`ntpath.isabs` shim replaces
    the step 12d validators patches; `certbot` entry point restores it).
  - Overlay files that collide with an upstream file now fail the build; `os.uname` is deliberately **not** shimmed (it would change
    `platform.uname()`), the two `nginx.conf` replacements stay.
  - New: `port-check.yml` (+ `port-check.sh`, `test-port.py`, `check-port.py`, `scan-hazards.py` + `hazards.baseline.txt`), nightly
    check against the newest upstream release with an issue on failure. `.gitattributes` keeps the patches byte-exact.
  Verified on Linux only: the patched tree differs from the pre-refactor output exactly in the files whose edits became shims; the
  port-check passes (15 unit tests incl. the Lua proxy under LuaJIT/lupa with a fake ffi, syntax of 143 changed files, hazard scan);
  `patchtool` re-import/export is byte-stable and the 3-way rebase and the conflict path were exercised against a scratch clone.
  **Not verified (only the smoke test can):** the ffi proxy against the real LuaJIT FFI on Windows (symbol resolution via ws2_32/ucrtbase,
  cdata function values returned through a Lua `__index`, `ffi.gc(file, <Lua function>)` in `stream_lock.lua`), that no module that matters
  is loaded *before* `wincompat` (anything that is gets the real `ffi`), `io.open` wrapper side effects, the `ntpath.isabs` shim under
  Windows Python 3.14 (also: certbot's restore), and `git apply` with `GIT_CEILING_DIRECTORIES` on the Windows runner. The eighth run
  (below) worked with the pre-refactor per-call-site patches: if the smoke test regresses, suspect the shims first
  (`git diff` of `windows/overlay` shows what replaced what); the old script is in git history.

- **Ninth CI run** (its fixes were cherry-picked from `main` onto the refactored layout: patches 8 and 9 and step 3b are the migrated
  `apply-windows-port.py` edits): the eighth run's curl fix **worked**: everything up to "bwctl status / test / reload" passed (healthz, proxy 200,
  default server 301). It failed in `bwctl reload-scheduler` with `The scheduler is not running (error 5)`: error 5 is ACCESS_DENIED,
  not "missing". The scheduler (SYSTEM) creates `Global\bunkerweb_scheduler_reload` with the default DACL of its token, which gives
  Administrators read/execute but not EVENT_MODIFY_STATE. Fixes written, not yet re-run: `create_admin_event` in `bwsvc.py`
  (explicit SDDL `D:(A;;0x1f0003;;;SY)(A;;0x1f0003;;;BA)`), `bwctl.py` prints "Access denied" for error 5. Other findings from the
  same logs (all fixes unverified): (a) the scheduler's second generator run failed with `PermissionError [WinError 5]` in
  `sync_tree.py` `os.replace(...modsecurity-rules.conf.modsec -> etc\nginx\...)`; root cause not proven (reader without
  `FILE_SHARE_DELETE`, read-only attribute?), the patch `sync-tree-retry-replacing-a-live-generated-file-on-windows` (`_bw_replace`) retries up to 20 s and clears the read-only bit; (b) the
  `ssl_ecdh_curve ... could not bind libssl` warning: Templator only looked for `libssl.so*`, the patch `templator-load-the-shipped-libssl-...` now loads the shipped
  `sbin\libssl-3*.dll` (`winmode=8`), `build-native.sh` ships and requires it; (c) `DNS_RESOLVERS` defaulted to Docker's `127.0.0.11`,
  hence `WSARecv() failed (10054)`, failing DNSBL lookups and a slow `init_worker`: step 3b of `apply-windows-port.py` changes the default to the public
  resolvers of the Linux packages. Benign warnings left alone: Pro licence key missing, no DNSBL ignore/whitelist URL configured,
  nginx `[TIMER-0] worker is exiting` on reload, `bunkerweb-api` Stopped (Manual by design), and one `database is locked` retry of
  the scheduler right after the UI service started (SQLite race that upstream's retry resolves). The `Diagnostics` step now has a
  "Warnings and errors" group over all logs. Spamhaus may refuse queries from public resolvers, so some DNSBL error lines can remain.
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
- After a green run (the pre-release `v1.6.18-rc5-win1` appears by itself): trial on a non-critical server including a Let's Encrypt *staging* issuance.
- Operator guide and installer have never run on a real server. Do not describe anything as "tested" beyond what the smoke test
  in a green CI run covers.

## Out of scope / do not do

- Do not turn this into a fork of BunkerWeb or vendor modified upstream files into the repo.
- Do not add container, VM or WSL based alternatives: they are explicitly ruled out by the requirements.
- Do not store secrets (tokens, passwords, API keys) in the repo. The operator's `variables.env` with their real hosts and
  backends lives in their own private configuration repo. Examples in the docs use generic names such as `app.example.com`.
