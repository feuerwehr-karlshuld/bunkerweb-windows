#!/usr/bin/env bash
# BunkerWeb for Windows - native build (MSYS2 UCRT64 shell, GCC + UCRT)
#
# Builds from the *patched copy* of bunkerweb/src (see apply-windows-port.py):
#   * LuaJIT (lua51.dll)
#   * nginx.exe with every module BunkerWeb loads dynamically on Linux linked in statically:
#     ngx_devel_kit, lua-nginx-module, stream-lua-nginx-module, lua-upstream-nginx-module,
#     headers-more, cookie-flag, brotli, ModSecurity-nginx (against the MSVC-built libModSecurity.dll)
#   * Lua C modules: cjson, gd
#   * pure Lua libraries (lua-resty-*, lualogging, geoip, base64, ffi-zlib, ...)
#   * runtime DLLs and tools (openssl.exe, sqlite3.exe, libmagic)
#
# Usage: build-native.sh <patched-src> <modsecurity-dir> <out-dir>
#   <modsecurity-dir> contains libModSecurity.dll (output of build-modsecurity.ps1)
#
# Output layout (<out-dir>):
#   sbin/        nginx.exe, lua51.dll, all runtime DLLs (incl. libModSecurity + MSVC CRT)
#   bin/         openssl.exe, sqlite3.exe (+ DLLs they need are in sbin, both dirs are in PATH)
#   deps/lib/    libmaxminddb.so, lua/ (Lua modules, cjson.so, gd.so, libz.so)
#   ssl/         openssl.cnf
#   misc/        magic.mgc
#   nginx-conf/  mime.types
#   BUILDINFO    versions

set -euo pipefail

if [ "$#" -ne 3 ]; then
	echo "usage: $0 <patched-src> <modsecurity-dir> <out-dir>" >&2
	exit 2
fi

SRC="$(cd "$1" && pwd)"
MODSEC="$(cd "$2" && pwd)"
mkdir -p "$3"
OUT="$(cd "$3" && pwd)"
D="$SRC/deps/src"
WORK="$OUT/.work"
NPROC="$(nproc)"
PREFIX_UCRT="${MINGW_PREFIX:-/ucrt64}"

LUA_OUT="$OUT/deps/lib/lua"
mkdir -p "$OUT/sbin" "$OUT/bin" "$LUA_OUT/resty" "$OUT/ssl" "$OUT/misc" "$OUT/nginx-conf" "$WORK"

# GCC >= 14 turns these into errors by default, the vendored C sources predate that
RELAXED_CFLAGS="-Wno-error=incompatible-pointer-types -Wno-error=int-conversion -Wno-error=implicit-function-declaration"

group() { echo "::group::$*"; }
endgroup() { echo "::endgroup::"; }

[ -f "$SRC/WINDOWS_PORT" ] || { echo "$SRC is not a patched copy (run apply-windows-port.py first)" >&2; exit 1; }
[ -f "$MODSEC/libModSecurity.dll" ] || { echo "libModSecurity.dll not found in $MODSEC" >&2; exit 1; }

# ------------------------------------------------------------------------------------- LuaJIT
group "LuaJIT"
make -C "$D/luajit" -j"$NPROC" CC=gcc BUILDMODE=dynamic
test -f "$D/luajit/src/lua51.dll"
cp "$D/luajit/src/lua51.dll" "$OUT/sbin/"
cp "$D/luajit/src/luajit.exe" "$OUT/bin/"
mkdir -p "$LUA_OUT/jit"
cp "$D/luajit/src/jit/"*.lua "$LUA_OUT/jit/"
LUAJIT_SRC="$D/luajit/src"
endgroup

# ------------------------------------------------------------------------------------- Lua C modules
group "lua-cjson"
(
	cd "$D/lua-cjson"
	# shellcheck disable=SC2086
	gcc -O2 -Wall $RELAXED_CFLAGS -shared -I"$LUAJIT_SRC" lua_cjson.c strbuf.c fpconv.c -L"$LUAJIT_SRC" -llua51 -o "$LUA_OUT/cjson.so"
	mkdir -p "$LUA_OUT/cjson"
	cp lua/cjson/util.lua "$LUA_OUT/cjson/"
)
endgroup

group "lua-gd"
(
	cd "$D/lua-gd"
	# The feature list matches what MSYS2's libgd provides; XPM is not available there.
	# shellcheck disable=SC2086
	gcc -O2 -Wall $RELAXED_CFLAGS -shared -I"$LUAJIT_SRC" '-DVERSION="2.0.33r3"' \
		-DGD_PNG -DGD_JPEG -DGD_GIF -DGD_FREETYPE -DGD_FONTCONFIG \
		luagd.c -L"$LUAJIT_SRC" -llua51 -lgd -o "$LUA_OUT/gd.so"
)
endgroup

# ------------------------------------------------------------------------------------- pure Lua libraries
group "Lua libraries"
cp -r "$D/lua-resty-core/lib/"* "$LUA_OUT/"
cp -r "$D/lua-resty-lrucache/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-dns/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-session/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-random/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-string/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-http/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-ipmatcher/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-redis/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-upload/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-lock/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-openssl/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-redis-connector/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-mlcache/lib/resty/"* "$LUA_OUT/resty/"
cp -r "$D/lua-resty-template/lib/resty/"* "$LUA_OUT/resty/"
cp "$D/lua-resty-env/src/resty/env.lua" "$LUA_OUT/resty/"
cp "$D/resty-redis-cluster/lib/resty/rediscluster.lua" "$D/resty-redis-cluster/lib/resty/xmodem.lua" "$LUA_OUT/resty/"
cp -r "$D/lualogging/src/"* "$LUA_OUT/"
cp -r "$D/luajit-geoip/geoip" "$LUA_OUT/"
cp "$D/lbase64/base64.lua" "$LUA_OUT/"
cp "$D/lua-ffi-zlib/lib/ffi-zlib.lua" "$LUA_OUT/"
# lua-resty-signal (POSIX kill) is replaced by bunkerweb/wincompat.lua; luasocket/luasec are not
# used by BunkerWeb's core plugins and are not built.
endgroup

# ------------------------------------------------------------------------------------- libModSecurity import library
group "libModSecurity import library"
MODSEC_LIB="$WORK/modsecurity-lib"
rm -rf "$MODSEC_LIB" && mkdir -p "$MODSEC_LIB"
(
	cd "$MODSEC_LIB"
	cp "$MODSEC/libModSecurity.dll" .
	gendef - libModSecurity.dll > full.def
	{
		echo 'LIBRARY "libModSecurity.dll"'
		echo 'EXPORTS'
		grep -E '^msc_[A-Za-z0-9_]+' full.def | sed -e 's/[[:space:]]*;.*$//' -e 's/[[:space:]]*$//'
	} > modsecurity.def
	count="$(grep -c '^msc_' modsecurity.def || true)"
	echo "msc_* exports: $count"
	[ "$count" -gt 10 ] || { echo "libModSecurity.dll does not export the msc_* API" >&2; cat full.def | head -50; exit 1; }
	dlltool -d modsecurity.def -D libModSecurity.dll -l libmodsecurity.dll.a
	rm -f libModSecurity.dll
)
endgroup

# ------------------------------------------------------------------------------------- nginx
group "nginx: prepare sources"
cp "$SRC/deps/misc/ngx_http_modsecurity_access.c" "$D/modsecurity-nginx/src/"
mkdir -p "$D/ngx_brotli/deps"
if [ -d "$D/brotli" ] && [ ! -d "$D/ngx_brotli/deps/brotli/c" ]; then
	rm -rf "$D/ngx_brotli/deps/brotli"
	mv "$D/brotli" "$D/ngx_brotli/deps/brotli"
fi
cd "$D/nginx"
[ -f configure ] || cp auto/configure ./configure
chmod +x configure
NGINX_VERSION="$(sed -n 's/^#define NGINX_VERSION *"\(.*\)"/\1/p' src/core/nginx.h)"
echo "nginx $NGINX_VERSION"
endgroup

group "nginx: configure"
export LUAJIT_INC="$LUAJIT_SRC"
export LUAJIT_LIB="$LUAJIT_SRC"
export MODSECURITY_INC="$D/modsecurity/headers"
export MODSECURITY_LIB="$MODSEC_LIB"

# Paths compiled into nginx.exe use the drive letter: nginx for Windows resolves "/..." paths
# against the drive of its prefix, BunkerWeb's generated configuration only uses "/..." paths.
./configure \
	--with-cc=gcc \
	--with-cc-opt="-O2 -DFD_SETSIZE=4096 -std=gnu11 -Wno-error $RELAXED_CFLAGS -Wno-deprecated-declarations" \
	--prefix=C:/usr/share/bunkerweb \
	--sbin-path=C:/usr/sbin/nginx.exe \
	--conf-path=C:/etc/nginx/nginx.conf \
	--pid-path=C:/var/run/bunkerweb/nginx.pid \
	--lock-path=C:/var/run/bunkerweb/nginx.lock \
	--error-log-path=C:/var/log/bunkerweb/error.log \
	--http-log-path=C:/var/log/bunkerweb/access.log \
	--http-client-body-temp-path=C:/var/tmp/bunkerweb/client_temp \
	--http-proxy-temp-path=C:/var/tmp/bunkerweb/proxy_temp \
	--http-fastcgi-temp-path=C:/var/tmp/bunkerweb/fastcgi_temp \
	--http-uwsgi-temp-path=C:/var/tmp/bunkerweb/uwsgi_temp \
	--http-scgi-temp-path=C:/var/tmp/bunkerweb/scgi_temp \
	--with-select_module \
	--with-pcre-jit \
	--with-http_ssl_module \
	--with-http_v2_module \
	--with-http_realip_module \
	--with-http_addition_module \
	--with-http_sub_module \
	--with-http_dav_module \
	--with-http_flv_module \
	--with-http_mp4_module \
	--with-http_gunzip_module \
	--with-http_gzip_static_module \
	--with-http_auth_request_module \
	--with-http_random_index_module \
	--with-http_secure_link_module \
	--with-http_slice_module \
	--with-http_stub_status_module \
	--with-stream \
	--with-stream_ssl_module \
	--with-stream_realip_module \
	--with-stream_ssl_preread_module \
	--add-module="$D/ngx_devel_kit" \
	--add-module="$D/lua-nginx-module" \
	--add-module="$D/stream-lua-nginx-module" \
	--add-module="$D/lua-upstream-nginx-module" \
	--add-module="$D/headers-more-nginx-module" \
	--add-module="$D/nginx_cookie_flag_module" \
	--add-module="$D/ngx_brotli" \
	--add-module="$D/modsecurity-nginx" \
	|| { echo "--- objs/autoconf.err (tail) ---"; tail -n 80 objs/autoconf.err || true; exit 1; }
endgroup

group "nginx: link OpenSSL statically"
# lua-resty-openssl calls libcrypto/libssl through ffi.C. On Windows ffi.C only sees symbols
# exported by the executable, so OpenSSL is linked into nginx.exe (whole archive) and exported
# together with nginx's own symbols (--export-all-symbols, needed by lua-resty-core as well).
SSL_A="$(cygpath -m "$PREFIX_UCRT/lib/libssl.a")"
CRYPTO_A="$(cygpath -m "$PREFIX_UCRT/lib/libcrypto.a")"
[ -f "$SSL_A" ] && [ -f "$CRYPTO_A" ] || { echo "static OpenSSL libraries not found" >&2; exit 1; }
grep -q -- '-lssl -lcrypto' objs/Makefile || { echo "objs/Makefile: '-lssl -lcrypto' not found" >&2; grep -n 'lssl\|lcrypto' objs/Makefile || true; exit 1; }
sed -i \
	-e "s#-lssl -lcrypto#-Wl,--whole-archive $SSL_A $CRYPTO_A -Wl,--no-whole-archive -lbcrypt -lcrypt32 -luser32 -lgdi32 -ladvapi32 -lws2_32#" \
	objs/Makefile
grep -n 'whole-archive' objs/Makefile
endgroup

group "nginx: build"
make -j"$NPROC" -f objs/Makefile
test -f objs/nginx.exe
cp objs/nginx.exe "$OUT/sbin/"
cp conf/mime.types "$OUT/nginx-conf/"
endgroup

# ------------------------------------------------------------------------------------- runtime DLLs and tools
group "runtime DLLs and tools"
cp "$PREFIX_UCRT/bin/openssl.exe" "$PREFIX_UCRT/bin/sqlite3.exe" "$OUT/bin/"
cp "$PREFIX_UCRT/etc/ssl/openssl.cnf" "$OUT/ssl/" 2>/dev/null || cp "$PREFIX_UCRT/ssl/openssl.cnf" "$OUT/ssl/"
MAGIC_MGC="$(ls "$PREFIX_UCRT"/share/misc/magic.mgc 2>/dev/null || true)"
[ -n "$MAGIC_MGC" ] && cp "$MAGIC_MGC" "$OUT/misc/"

# libraries BunkerWeb's Lua code loads with ffi.load() under fixed Linux names
cp "$PREFIX_UCRT/bin/zlib1.dll" "$LUA_OUT/libz.so"
MMDB_DLL="$(ls "$PREFIX_UCRT"/bin/libmaxminddb*.dll | head -n 1)"
cp "$MMDB_DLL" "$OUT/deps/lib/libmaxminddb.so"

# libmagic for python-magic (it looks for libmagic-1.dll / magic1.dll on PATH)
for dll in "$PREFIX_UCRT"/bin/libmagic*.dll; do
	[ -f "$dll" ] && cp "$dll" "$OUT/sbin/"
done

# every MinGW DLL any of our binaries needs, recursively (ldd resolves the whole tree)
collect_dlls() {
	ldd "$1" 2>/dev/null | awk '{print $3}' | grep -i "^$PREFIX_UCRT/bin/" || true
}
for binary in "$OUT/sbin/nginx.exe" "$OUT/sbin/lua51.dll" "$LUA_OUT/cjson.so" "$LUA_OUT/gd.so" \
	"$OUT/bin/openssl.exe" "$OUT/bin/sqlite3.exe" "$OUT/deps/lib/libmaxminddb.so" "$OUT/sbin/"libmagic*.dll; do
	[ -f "$binary" ] || continue
	collect_dlls "$binary"
done | sort -u | while read -r dll; do
	[ -f "$OUT/sbin/$(basename "$dll")" ] || cp "$dll" "$OUT/sbin/"
done

cp "$MODSEC/"*.dll "$OUT/sbin/"
endgroup

group "verify"
cd "$OUT/sbin"
PATH="$OUT/sbin:$PATH" ./nginx.exe -V 2>&1
missing="$(PATH="$OUT/sbin:$PATH" ldd ./nginx.exe | grep -i 'not found' || true)"
if [ -n "$missing" ]; then
	echo "nginx.exe has unresolved DLLs:" >&2
	echo "$missing" >&2
	exit 1
fi
objdump -p nginx.exe > "$WORK/nginx-exports.txt"
for symbol in OpenSSL_version_num SSL_CTX_new EVP_PKEY_new ngx_http_lua_ffi_get_resp_status; do
	grep -qE "[][[:space:]]${symbol}[[:space:]]*\$" "$WORK/nginx-exports.txt" || { echo "nginx.exe does not export $symbol (needed by ffi.C)" >&2; exit 1; }
done
echo "nginx.exe exports the OpenSSL and lua-resty-core FFI symbols"
{
	echo "nginx=$NGINX_VERSION"
	echo "luajit=$(cd "$D/luajit" && cat .relver 2>/dev/null || echo unknown)"
	echo "openssl=$("$OUT/bin/openssl.exe" version 2>/dev/null | tr -d '\r' || echo unknown)"
	echo "msys2-ucrt64-gcc=$(gcc -dumpversion)"
} > "$OUT/BUILDINFO"
cat "$OUT/BUILDINFO"
ls -la "$OUT/sbin"
rm -rf "$WORK"
endgroup
