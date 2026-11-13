<#
.SYNOPSIS
  Builds libModSecurity.dll (ModSecurity v3, vendored in bunkerweb/src/deps/src/modsecurity) with MSVC.

.DESCRIPTION
  Uses ModSecurity's own Windows build (build/win32, conan 2 + CMake + Visual Studio 2022).
  The library is linked by the MinGW-built nginx through its plain C API (msc_*), both sides use
  the Universal CRT, so memory allocated by ModSecurity can be freed by nginx.

  Output (in -Out):
    libModSecurity.dll
    *.dll                Visual C++ runtime DLLs (Microsoft.VC143.CRT)

.PARAMETER Src
  Path to the *patched copy* of bunkerweb/src (see apply-windows-port.py).
#>
param(
    [Parameter(Mandatory)] [string] $Src,
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
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Out = (Resolve-Path $Out).Path

$deps = Join-Path $Src 'deps\src'
$ms = Join-Path $deps 'modsecurity'
$win32 = Join-Path $ms 'build\win32'
$build = Join-Path $win32 'build'

# Same preparation as src/deps/install.sh: libinjection and mbedtls live in others/
foreach ($name in 'libinjection', 'mbedtls') {
    $target = Join-Path $ms "others\$name"
    $source = Join-Path $deps $name
    if (Test-Path $source) {
        if (Test-Path $target) { Remove-Item -Recurse -Force $target }
        Move-Item $source $target
    }
    if (-not (Test-Path (Join-Path $target '*'))) { throw "ModSecurity dependency $name is missing in $target" }
}

Invoke-Checked 'conan profile' { conan profile detect --force }

Push-Location $win32
try {
    Invoke-Checked 'conan install' {
        conan install . -s compiler.cppstd=17 --output-folder=build --build=missing `
            --settings=build_type=Release --settings=arch=x86_64
    }
    Invoke-Checked 'cmake configure' {
        cmake --fresh -S . -B build -G 'Visual Studio 17 2022' -A x64 `
            "-DCMAKE_TOOLCHAIN_FILE=$build\conan_toolchain.cmake" `
            -DUSE_ASAN=OFF -DWITH_LMDB=OFF -DWITH_LUA=ON -DWITH_LIBXML2=ON -DWITH_MAXMIND=ON -DWITH_CURL=ON
    }
    Invoke-Checked 'cmake build' {
        cmake --build build --config Release --target libModSecurity --parallel
    }
}
finally {
    Pop-Location
}

$dll = Get-ChildItem -Path $build -Recurse -Filter 'libModSecurity.dll' | Select-Object -First 1
if (-not $dll) { throw 'libModSecurity.dll was not produced' }
Copy-Item $dll.FullName $Out
Write-Host "libModSecurity.dll: $($dll.FullName) ($([math]::Round($dll.Length / 1MB, 1)) MB)"

# Visual C++ runtime next to the DLL (nginx.exe lives in the same directory on the target)
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -latest -products * -property installationPath
$crt = Get-ChildItem -Path (Join-Path $vs 'VC\Redist\MSVC') -Recurse -Directory -Filter 'Microsoft.VC14*.CRT' |
    Where-Object { $_.FullName -match '\\x64\\' -and $_.FullName -notmatch 'onecore|debug_nonredist' } |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $crt) { throw 'Visual C++ runtime redistributables not found' }
Copy-Item (Join-Path $crt.FullName '*.dll') $Out
Write-Host "CRT: $($crt.FullName)"
Get-ChildItem $Out | Format-Table Name, Length
