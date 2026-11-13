@echo off
setlocal
rem BunkerWeb CLI (ban/unban/bans/plugin commands) - same as /usr/bin/bwcli on Linux.
rem BunkerWeb resolves POSIX paths (/etc/bunkerweb, /var/lib/bunkerweb ...) relative to the current drive (C:); the build prefixes them with /bunkerweb.
cd /d C:\bunkerweb
set "PYTHONUTF8=1"
set "PYTHONPATH=C:\bunkerweb\usr\share\bunkerweb\deps\python"
set "OPENSSL_CONF=C:\bunkerweb\usr\ssl\openssl.cnf"
set "PATH=C:\bunkerweb\usr\sbin;C:\bunkerweb\usr\bin;C:\bunkerweb\usr\share\bunkerweb\python;%PATH%"
"C:\bunkerweb\usr\share\bunkerweb\python\python.exe" "C:\bunkerweb\usr\share\bunkerweb\cli\main.py" %*
exit /b %ERRORLEVEL%
