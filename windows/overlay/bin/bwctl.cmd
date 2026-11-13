@echo off
setlocal
rem BunkerWeb for Windows control helper - see "bwctl" without arguments for usage.
cd /d C:\bunkerweb
set "PYTHONUTF8=1"
set "PYTHONPATH=C:\bunkerweb\usr\share\bunkerweb\deps\python"
"C:\bunkerweb\usr\share\bunkerweb\python\python.exe" "C:\bunkerweb\usr\share\bunkerweb\windows\bwctl.py" %*
exit /b %ERRORLEVEL%
