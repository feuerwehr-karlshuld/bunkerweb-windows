@echo off
setlocal
rem BunkerWeb for Windows control helper - see "bwctl" without arguments for usage.
cd /d C:\
set "PYTHONUTF8=1"
set "PYTHONPATH=C:\usr\share\bunkerweb\deps\python"
"C:\usr\share\bunkerweb\python\python.exe" "C:\usr\share\bunkerweb\windows\bwctl.py" %*
exit /b %ERRORLEVEL%
