@echo off
setlocal
rem BunkerWeb CLI (ban/unban/bans/plugin commands) - same as /usr/bin/bwcli on Linux.
rem BunkerWeb resolves POSIX paths (/etc/bunkerweb, /var/lib/bunkerweb ...) relative to the current drive.
cd /d C:\
set "PYTHONUTF8=1"
set "PYTHONPATH=C:\usr\share\bunkerweb\deps\python"
set "OPENSSL_CONF=C:\usr\ssl\openssl.cnf"
set "PATH=C:\usr\sbin;C:\usr\bin;C:\usr\share\bunkerweb\python;%PATH%"
"C:\usr\share\bunkerweb\python\python.exe" "C:\usr\share\bunkerweb\cli\main.py" %*
exit /b %ERRORLEVEL%
