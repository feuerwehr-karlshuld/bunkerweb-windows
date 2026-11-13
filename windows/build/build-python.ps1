<#
.SYNOPSIS
  Builds the Python runtime of BunkerWeb for Windows.

.DESCRIPTION
  Output (in -Out):
    python\        a private copy of the CPython installation this script runs with (setup-python),
                   plus waitress (WSGI server replacing gunicorn for the web UI) and the
                   Windows compatibility shims (sitecustomize, fcntl, pwd, grp)
    deps\python\   BunkerWeb's Python dependencies, installed exactly like the Linux packages do
                   (pip --require-hashes --target), with the certbot DNS alias patch applied

.PARAMETER Src
  Path to bunkerweb/src (original or patched copy - only requirements and deps/misc are read).
.PARAMETER Windows
  Path to the windows/ directory of this repository.
#>
param(
    [Parameter(Mandatory)] [string] $Src,
    [Parameter(Mandatory)] [string] $Windows,
    [Parameter(Mandatory)] [string] $Out
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Checked {
    param([string] $What, [scriptblock] $Block)
    Write-Host "::group::$What"
    & $Block
    $code = $LASTEXITCODE
    Write-Host '::endgroup::'
    if ($code -ne 0) { throw "$What failed with exit code $code" }
}

$Src = (Resolve-Path $Src).Path
$Windows = (Resolve-Path $Windows).Path
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Out = (Resolve-Path $Out).Path

$WAITRESS_VERSION = '3.0.2'

# ------------------------------------------------------------------ private CPython copy
$basePrefix = (& python -c 'import sys; print(sys.base_prefix)').Trim()
$pyVersion = (& python -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])').Trim()
Write-Host "Using CPython $pyVersion from $basePrefix"
$py = Join-Path $Out 'python'
if (Test-Path $py) { Remove-Item -Recurse -Force $py }
Copy-Item -Recurse $basePrefix $py
Copy-Item (Join-Path $py 'python.exe') (Join-Path $py 'python3.exe')
# do not ship test suites and caches of the build machine
foreach ($sub in 'Lib\test', 'Lib\idlelib\idle_test', 'Lib\tkinter\test') {
    $path = Join-Path $py $sub
    if (Test-Path $path) { Remove-Item -Recurse -Force $path }
}
Get-ChildItem -Path $py -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
$python = Join-Path $py 'python.exe'

Invoke-Checked 'pip upgrade' { & $python -m pip install --no-cache-dir --upgrade pip }
Invoke-Checked 'waitress' { & $python -m pip install --no-cache-dir "waitress==$WAITRESS_VERSION" }

# ------------------------------------------------------------------ BunkerWeb dependencies
$req = Join-Path $Out 'requirements-windows.txt'
Invoke-Checked 'merge requirements' { & $python (Join-Path $Windows 'build\prepare-requirements.py') $Src $req }

$deps = Join-Path $Out 'deps\python'
if (Test-Path $deps) { Remove-Item -Recurse -Force $deps }
New-Item -ItemType Directory -Force -Path $deps | Out-Null
# The lock files are compiled on Linux, so they are complete for Linux but lack the packages that
# are only required on Windows. --no-deps keeps pip from rejecting those missing pins under
# --require-hashes; the Windows-only extras are installed afterwards.
Invoke-Checked 'pip install BunkerWeb dependencies' {
    & $python -m pip install --no-cache-dir --require-hashes --no-deps --ignore-installed --target $deps -r $req
}

# Windows-only dependencies that never show up in the Linux lock files:
#   pywin32   certbot (file permissions, locking) imports win32security & co. on Windows
#   colorama  click/tqdm/... colour output on the Windows console
Invoke-Checked 'pip install Windows-only dependencies' {
    & $python -m pip install --no-cache-dir --no-deps --upgrade --target $deps 'pywin32>=300' colorama
}

# Report (but do not fail on) any other dependency that --no-deps left unresolved.
$env:PYTHONPATH = $deps
& $python -m pip check
if ($LASTEXITCODE -ne 0) { Write-Host '::warning::pip check reported unresolved dependencies, see the log' }
Remove-Item Env:\PYTHONPATH

# certbot DNS alias support, same patch as the Linux packages
$patch = Join-Path $Src 'deps\misc\certbot-dns-alias.patch'
$patchExe = 'C:\Program Files\Git\usr\bin\patch.exe'
Invoke-Checked 'certbot-dns-alias.patch (dry run)' { & $patchExe -p1 -F0 --dry-run -d $deps -i $patch }
Invoke-Checked 'certbot-dns-alias.patch' { & $patchExe -p1 -F0 --no-backup-if-mismatch -d $deps -i $patch }
if (-not (Select-String -Quiet -Path (Join-Path $deps 'certbot\plugins\dns_common.py') -Pattern '_load_domain_aliases')) {
    throw 'certbot-dns-alias.patch was not applied'
}

# ------------------------------------------------------------------ Windows shims (last, so pip itself is not affected)
$site = Join-Path $py 'Lib\site-packages'
Copy-Item (Join-Path $Windows 'overlay\python-site\*.py') $site
Get-ChildItem -Path $deps -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force

# smoke test: the shims and the main imports must load (python-magic needs libmagic from the native job)
$env:PYTHONPATH = $deps
Invoke-Checked 'import check' {
    & $python -c "import sitecustomize, fcntl, pwd, grp, os; assert hasattr(os, '_bw_shims'); import sqlalchemy, flask, fastapi, certbot, waitress, uvicorn, cryptography; import win32api, win32security, ntsecuritycon; import certbot.compat.filesystem; print('imports ok')"
}
Remove-Item Env:\PYTHONPATH

"python=$pyVersion" | Set-Content -Encoding ascii (Join-Path $Out 'BUILDINFO-python')
