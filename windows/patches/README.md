# The patch series

Everything the port changes in BunkerWeb's sources, in one place, with the reason and the exit strategy for each change.
The upstream submodule (`../../bunkerweb`) is never modified: the build copies `bunkerweb/src` and
[`../build/apply-windows-port.py`](../build/apply-windows-port.py) applies, in this order:

| Layer | Where | What goes there | What happens when upstream changes |
|---|---|---|---|
| 1. Runtime shims | `../overlay/src/bw/lua/bunkerweb/{wincompat,winffi}.lua`, `../overlay/python-site/sitecustomize.py` | Everything that can be absorbed at runtime: `ffi.C` lookups (winsock, `setenv`, `flock` emulation), binary `io.open`, `resty.signal`, `/proc/uptime`, `ntpath.isabs`, POSIX modules, `subprocess` | Nothing breaks until upstream calls something the shim does not know. The hazard scan (below) lists new calls. |
| 2. **This patch series** | `*.patch`, order in `.patches` | Multi-line source changes with context (git format-patch files) | `git apply` fails if the hunk no longer matches; `patchtool.py import --rev` rebases the series with git's 3-way merge. |
| 3. One-line replacements | `apply-windows-port.py` (`rep`, `rep_re`) | Single-line, anchored, counted substitutions | The build fails and lists every anchor whose count changed. |
| 4. Path rewrite | `../build/pathrewrite.py` | `/etc/bunkerweb` -> `/bunkerweb/etc/bunkerweb`, ... in `.py/.lua/.conf/.json/.modsec` | Pattern based, so new files are covered automatically; golden tests in `../build/test-port.py`. |

Rule of thumb: prefer a shim (1), then a pattern (4), then a patch (2) for anything with context, and only then a
replacement (3). The fewer source anchors, the fewer things an upstream release can break.

The whole chain is also checked, without Windows and in about a minute, by `bash windows/build/port-check.sh`
(CI: `.github/workflows/port-check.yml`, on every push and nightly against the newest BunkerWeb release).

## Ledger

| # | Patch | Files | Why | Upstream | How to tell it is obsolete |
|---|---|---|---|---|---|
| 1 | `switch-http-3-quic-off-for-good` | `default-server-http.conf`, `ssl-certificate-lua.conf`, `limitconn.conf`, `misc/plugin.json` | nginx for Windows has no `http_v3_module`; the `http3` directive and `$http3` do not exist | Windows specific | `nginx -V` of the Windows build lists `--with-http_v3_module` |
| 2 | `make-the-health-check-port-a-global-setting` | `healthcheck.conf`, `settings.json` | The port was hardcoded to 6000; a server with other software needs to choose it | Could be proposed upstream (draft below) | upstream's `healthcheck.conf` has an equivalent setting |
| 3 | `sync-tree-take-the-uptime-from-gettickcount64-on-windows` | `common/gen/sync_tree.py` | No `/proc/uptime` | Could be proposed upstream (a `sys.platform` branch) | `sync_tree.py` no longer reads `/proc/uptime` directly |
| 4 | `nginx-configure-detect-the-system-openssl-pcre2-and-zlib-on` | `deps/src/nginx/auto/lib/{openssl,pcre,zlib}/conf` | nginx's configure skips system libraries on win32 (MSVC source trees); the port builds with MSYS2/GCC | Windows specific | the native build passes without it |
| 5 | `modsecurity-nginx-always-use-the-empty-dd-debug-macro` | `modsecurity-nginx/src/ddebug.h` | nginx for Windows does not define `NGX_HAVE_VARIADIC_MACROS` | Could go to ModSecurity-nginx | the native build compiles without it |
| 6 | `lua-resty-core-shdict-free-through-the-ucrt-on-windows` | `lua-resty-core/.../shdict.lua` | `free()` must come from the CRT that allocated the buffer; resty.core loads before `init_by_lua`, so the ffi proxy cannot help | Windows specific | wincompat's ffi proxy becomes active before resty.core loads |
| 7 | `templator-render-in-one-worker-thread-instead-of-forked` | `common/gen/Templator.py` | no `fork` on Windows | Windows specific | upstream's Templator works with `spawn` |
| 8 | `sync-tree-retry-replacing-a-live-generated-file-on-windows` | `common/gen/sync_tree.py` | `os.replace` over a file another process holds open (no `FILE_SHARE_DELETE`) or that is read-only fails with `WinError 5` | Windows specific | `sync_tree()` copes with a busy target on Windows |
| 9 | `templator-load-the-shipped-libssl-for-the-ssl-ecdh-curve` | `common/gen/Templator.py` | the `ssl_ecdh_curve` probe only looks for `libssl.so*`; the port ships `usr\sbin\libssl-3*.dll` (`build-native.sh`) | Windows specific | no `could not bind libssl` warning without it |
| 10 | `cache-restore-accept-the-installed-tmp-root-for-folder` | `common/utils/cache_restore.py` | `Path("/bunkerweb/var/tmp/bunkerweb").resolve()` gains a drive letter, the failover row's target does not (and `_check_path` only maps the cache/config roots), so every failover restore raised "Folder cache entry is outside the cache roots" | Windows specific | no such line in `scheduler.log` without it |

Every patch carries `Upstream:` and `Check:` lines in its commit message; `patchtool.py lint` (part of the port check)
refuses a patch without them.

Not patched any more, because a runtime shim covers it (this is what keeps the series short):
`ffi.C` calls in `api.lua`, `stream_lock.lua`, `iputils.lua`, `lua-resty-ipmatcher`, `lua-resty-openssl` (`inet_pton`,
`ntohl`, `flock`, `setenv`, ...), `resty.signal`, `ngx.process.get_master_pid`, `"w+"` -> `"w+b"` for uploads,
`/proc/uptime` in Lua, and `Path.is_absolute()` of rooted paths on Python >= 3.13.

## Working with the series

Needs only `git` and Python. Nothing is written to `bunkerweb/`; the work happens in a scratch repository
(`build/patchwork`, git-ignored).

```sh
# look at / change the series
python windows/build/patchtool.py import --force      # upstream tree of BASE + the series, as real commits
cd build/patchwork                                   # edit, `git commit` (subject = what, body = why,
                                                     # plus "Upstream: ..." and "Check: ..." lines), `git rebase -i`, ...
python windows/build/patchtool.py export             # writes *.patch, .patches and BASE (byte-stable)

# move to a new BunkerWeb release (see "Upgrading BunkerWeb" in ../README.md)
git -C bunkerweb fetch --tags && git -C bunkerweb checkout v1.6.18
python windows/build/patchtool.py import --force --rev v1.6.18   # git rebase --onto: a 3-way merge per patch
#   on a conflict: resolve in build/patchwork, `git add`, `git rebase --continue`
python windows/build/patchtool.py export
```

`BASE` is the upstream commit the patches were made against; `import` needs it to rebuild the starting tree, `export`
updates it. `.gitattributes` keeps the patch files byte-exact on every platform (a CRLF checkout would break `git apply`).

`git apply` has no fuzz: a patch either applies exactly or the build says which one does not. The merging happens only
in `patchtool.py import --rev`, where a human sees and resolves it.

## Hazard scan

`../build/scan-hazards.py` greps the *patched* tree for Linux-isms (new `ffi.C` symbols, `ffi.load` libraries, shell
commands, `/proc`, `fork`, signals, `isabs`, text-mode writes, ...) and compares the counts per file with
`../build/hazards.baseline.txt`. A new line or a higher count fails the port check; it is the early warning that upstream
started to call something nobody has looked at on Windows. Review it, add a shim or a patch if needed, then run
`python windows/build/scan-hazards.py build/src --update` and commit the baseline together with the fix.

## Candidate upstream contributions (nothing has been submitted)

These changes are portable and would make the next upgrade cheaper if upstream took them. Drafts only; whether to open
a pull request is the maintainer's decision.

- **Binary mode for uploaded archives** (`api.lua`): `io.open(tmp, "w+")` should be `"w+b"` (a no-op on Linux). On
  platforms with text-mode translation it corrupts every gzip stream that contains `0x0A`.
- **`os.pathsep` instead of `":"`** in `letsencrypt_utils.py` when splitting/joining `PYTHONPATH`.
- **`HEALTHCHECK_PORT`** (patch 2): the health check port as a global setting, default 6000.
- **`sys.platform` branch for the uptime** in `sync_tree.py` (patch 3).
