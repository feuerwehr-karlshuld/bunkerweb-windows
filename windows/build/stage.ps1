<#
.SYNOPSIS
  Assembles the BunkerWeb for Windows file tree. The tree mirrors the target drive (C:\), because
  BunkerWeb uses absolute POSIX paths (/usr/share/bunkerweb, /etc/bunkerweb, /var/lib/bunkerweb ...)
  everywhere and Windows resolves them against the current drive.

.DESCRIPTION
  <Out>\usr\share\bunkerweb\   BunkerWeb itself (same layout as the Linux packages) + python\ + windows\ + service\
  <Out>\usr\sbin\              nginx.exe and every DLL it needs
  <Out>\usr\bin\               busybox (sh), openssl, sqlite3, luajit, bwctl.cmd, bwcli.cmd
  <Out>\usr\ssl\openssl.cnf
  <Out>\usr\share\misc\magic.mgc
  <Out>\etc\nginx\mime.types
  <Out>\var\tmp\bunkerweb\{asn,country}.mmdb
#>
param(
    [Parameter(Mandatory)] [string] $Repo,        # bunkerweb submodule root (for misc/build)
    [Parameter(Mandatory)] [string] $Src,         # patched copy of bunkerweb/src
    [Parameter(Mandatory)] [string] $Windows,     # windows/ directory of this repository
    [Parameter(Mandatory)] [string] $Native,      # output of build-native.sh
    [Parameter(Mandatory)] [string] $Python,      # output of build-python.ps1
    [Parameter(Mandatory)] [string] $Busybox,     # busybox-w64 executable
    [Parameter(Mandatory)] [string] $WinSW,       # WinSW executable
    [Parameter(Mandatory)] [string] $Out
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Copy-Tree {
    param([string] $From, [string] $To)
    if (-not (Test-Path $From)) { throw "Missing build input: $From" }
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Copy-Item -Recurse -Force (Join-Path $From '*') $To
}

function Copy-One {
    param([string] $From, [string] $To)
    if (-not (Test-Path $From)) { throw "Missing build input: $From" }
    New-Item -ItemType Directory -Force -Path (Split-Path $To -Parent) | Out-Null
    Copy-Item -Force $From $To
}

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Out = (Resolve-Path $Out).Path
$bw = Join-Path $Out 'usr\share\bunkerweb'

# ------------------------------------------------------------------ BunkerWeb (as in src/linux/Dockerfile-*)
Copy-Tree "$Src\bw\loading"  "$bw\loading"
Copy-Tree "$Src\bw\lua"      "$bw\lua"
Copy-Tree "$Src\bw\misc"     "$bw\misc"
Copy-Tree "$Src\common\api"  "$bw\api"
Copy-Tree "$Src\common\cli"  "$bw\cli"
Copy-Tree "$Src\common\confs" "$bw\confs"
Copy-Tree "$Src\common\core" "$bw\core"
Copy-Tree "$Src\common\db"   "$bw\db"
Copy-Tree "$Src\common\gen"  "$bw\gen"
Copy-Tree "$Src\common\helpers" "$bw\helpers"
Copy-Tree "$Src\common\utils" "$bw\utils"
Copy-One  "$Src\common\settings.json" "$bw\settings.json"
Copy-Tree "$Src\scheduler"   "$bw\scheduler"
Copy-Tree "$Src\ui"          "$bw\ui"
Copy-Tree "$Src\api"         "$bw\api"
Copy-One  "$Src\VERSION"     "$bw\VERSION"
# the Windows port emulates the Linux integration (paths, services, jobs)
Set-Content -NoNewline -Encoding ascii -Path "$bw\INTEGRATION" -Value "Linux`n"

# geo databases are also seeded into /var/tmp/bunkerweb like on Linux
foreach ($db in 'asn.mmdb', 'country.mmdb') {
    Copy-One "$Src\bw\misc\$db" "$Out\var\tmp\bunkerweb\$db"
}

Write-Host '::group::merge_css_imports'
& (Join-Path $Python 'python\python.exe') -X utf8 "$Repo\misc\build\merge_css_imports.py" "$bw\ui\app\static\css\overrides.css"
if ($LASTEXITCODE -ne 0) { throw 'merge_css_imports.py failed' }
Write-Host '::endgroup::'

# ------------------------------------------------------------------ Python runtime and dependencies
Copy-Tree "$Python\python"      "$bw\python"
Copy-Tree "$Python\deps\python" "$bw\deps\python"

# ------------------------------------------------------------------ native build
Copy-Tree "$Native\sbin"        "$Out\usr\sbin"
Copy-Tree "$Native\bin"         "$Out\usr\bin"
Copy-Tree "$Native\deps\lib"    "$bw\deps\lib"
Copy-Tree "$Native\ssl"         "$Out\usr\ssl"
if (Test-Path "$Native\misc\magic.mgc") { Copy-One "$Native\misc\magic.mgc" "$Out\usr\share\misc\magic.mgc" }
Copy-One  "$Native\nginx-conf\mime.types" "$Out\etc\nginx\mime.types"

# ------------------------------------------------------------------ Windows glue (overlay)
Copy-Tree "$Windows\overlay\share" $bw      # windows\bwsvc.py, windows\bwctl.py, deps\python\bin\certbot
Copy-Tree "$Windows\overlay\bin"   "$Out\usr\bin"
Copy-One  $Busybox                 "$Out\usr\bin\busybox.exe"
Copy-One  $Busybox                 "$Out\usr\bin\sh.exe"

$svc = Join-Path $bw 'service'
New-Item -ItemType Directory -Force -Path $svc | Out-Null
foreach ($xml in Get-ChildItem "$Windows\service\*.xml") {
    Copy-Item $xml.FullName $svc
    Copy-Item $WinSW (Join-Path $svc ($xml.BaseName + '.exe'))
}

# ------------------------------------------------------------------ build information
$info = @()
foreach ($file in "$Native\BUILDINFO", "$Python\BUILDINFO-python") {
    if (Test-Path $file) { $info += Get-Content $file }
}
$info += "bunkerweb=$((Get-Content "$Src\VERSION" -Raw).Trim())"
$info += "busybox=$(Split-Path $Busybox -Leaf)"
$info += "winsw=$(Split-Path $WinSW -Leaf)"
$info | Set-Content -Encoding ascii "$bw\windows\BUILDINFO"
Get-Content "$bw\windows\BUILDINFO"

# sanity checks
foreach ($required in "$Out\usr\sbin\nginx.exe", "$Out\usr\sbin\lua51.dll", "$Out\usr\sbin\libModSecurity.dll",
    "$bw\python\python.exe", "$bw\python\python3.exe", "$bw\windows\bwsvc.py", "$bw\deps\lib\lua\cjson.so",
    "$bw\deps\lib\lua\libz.so", "$bw\deps\lib\libmaxminddb.so", "$bw\deps\python\bin\certbot",
    "$bw\lua\bunkerweb\wincompat.lua", "$Out\usr\bin\busybox.exe") {
    if (-not (Test-Path $required)) { throw "Staged tree is missing $required" }
}
$size = (Get-ChildItem -Recurse -File $Out | Measure-Object -Sum Length).Sum
Write-Host ("Staged {0:N0} MB in {1}" -f ($size / 1MB), $Out)
