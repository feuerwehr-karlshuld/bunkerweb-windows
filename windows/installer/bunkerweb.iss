; BunkerWeb for Windows - Inno Setup 6 script
;
; Build (see .github/workflows/bunkerweb-windows.yml):
;   ISCC.exe /DAppVersion=1.6.17 /DPortRevision=1 /DStageDir=<stage> /DLicenseFile=<bunkerweb\LICENSE.md> /DOutputDir=<dist> bunkerweb.iss
;
; The installation layout is fixed and entirely below C:\bunkerweb. BunkerWeb uses absolute POSIX paths (/etc/bunkerweb,
; /var/lib/bunkerweb, ...); the build rewrites them to /bunkerweb/etc/bunkerweb ..., which Windows resolves on the C: drive.
;   C:\bunkerweb\usr\share\bunkerweb   program files (replaced on every upgrade)
;   C:\bunkerweb\usr\sbin, C:\bunkerweb\usr\bin  nginx.exe + DLLs, tools (busybox, openssl, sqlite3, bwctl, bwcli)
;   C:\bunkerweb\etc\bunkerweb         configuration (variables.env, ui.env, scheduler.env, api.env, configs\, plugins\) - kept
;   C:\bunkerweb\var\lib\bunkerweb     database, certificates, job cache - kept
;   C:\bunkerweb\var\log\bunkerweb     logs - kept

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef PortRevision
  #define PortRevision "0"
#endif
#ifndef StageDir
  #define StageDir "..\..\build\stage"
#endif
#ifndef LicenseFile
  #define LicenseFile "..\..\bunkerweb\LICENSE.md"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

#define BW "C:\bunkerweb\usr\share\bunkerweb"
#define Svc "C:\bunkerweb\usr\share\bunkerweb\service"
#define PS "{sys}\WindowsPowerShell\v1.0\powershell.exe"

[Setup]
AppId={{6B0E1C8D-3E52-4F0B-9A55-2F1C9E7B4D21}
AppName=BunkerWeb for Windows
AppVersion={#AppVersion}-win{#PortRevision}
AppVerName=BunkerWeb {#AppVersion} for Windows (port revision {#PortRevision})
AppPublisher=Unofficial Windows port of BunkerWeb (Bunkerity)
AppPublisherURL=https://github.com/feuerwehr-karlshuld/reverse-proxy
AppSupportURL=https://github.com/feuerwehr-karlshuld/reverse-proxy
AppComments=BunkerWeb is developed by Bunkerity (https://github.com/bunkerity/bunkerweb) and licensed under the AGPLv3.
VersionInfoVersion={#AppVersion}.{#PortRevision}
DefaultDirName={#BW}
DisableDirPage=yes
UsePreviousAppDir=no
DisableProgramGroupPage=yes
UninstallFilesDir={commonappdata}\BunkerWeb\uninstall
UninstallDisplayName=BunkerWeb {#AppVersion} for Windows
UninstallDisplayIcon={#BW}\python\python.exe
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows Server 2019 / Windows 10 1809 or later
MinVersion=10.0.17763
LicenseFile={#LicenseFile}
OutputDir={#OutputDir}
OutputBaseFilename=bunkerweb-{#AppVersion}-win{#PortRevision}-x64-setup
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
SetupLogging=yes
ChangesEnvironment=yes
CloseApplications=no
RestartIfNeededByRun=no

[Messages]
WelcomeLabel2=This will install BunkerWeb [ver] on your server as native Windows services (nginx, scheduler, web UI, API).%n%nThis is an UNOFFICIAL port, it is not supported by Bunkerity.%n%nConfiguration lives in C:\bunkerweb\etc\bunkerweb, data in C:\bunkerweb\var\lib\bunkerweb. Both are kept on upgrade and uninstall.

[Tasks]
Name: "startservices"; Description: "Start the BunkerWeb services after installation"
Name: "webui"; Description: "Enable the web UI service (listens on 127.0.0.1:7000)"
Name: "firewall"; Description: "Allow inbound HTTP/HTTPS (TCP 80, 443) in Windows Firewall"

[Dirs]
Name: "C:\bunkerweb\etc\bunkerweb"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\plugins"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\pro\plugins"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\configs\http"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\configs\stream"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\configs\server-http"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\configs\server-stream"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\configs\default-server-http"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\configs\modsec"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\configs\modsec-crs"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\configs\crs-plugins-before"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\bunkerweb\configs\crs-plugins-after"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\etc\nginx"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\var\lib\bunkerweb"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\var\cache\bunkerweb"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\var\log\bunkerweb\service"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\var\run\bunkerweb"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\var\tmp\bunkerweb"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\var\www\html"; Flags: uninsneveruninstall
Name: "C:\bunkerweb\tmp\bunkerweb"; Flags: uninsneveruninstall

[InstallDelete]
; program files are replaced completely on upgrade (configuration and data live elsewhere)
Type: filesandordirs; Name: "{#BW}"

[Files]
Source: "{#StageDir}\usr\*"; DestDir: "C:\bunkerweb\usr"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\etc\nginx\mime.types"; DestDir: "C:\bunkerweb\etc\nginx"; Flags: ignoreversion
Source: "{#StageDir}\var\tmp\bunkerweb\*.mmdb"; DestDir: "C:\bunkerweb\var\tmp\bunkerweb"; Flags: onlyifdoesntexist uninsneveruninstall

[Registry]
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};C:\bunkerweb\usr\bin"; Check: NeedsAddPath('C:\bunkerweb\usr\bin')

[UninstallRun]
Filename: "{#PS}"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ""foreach ($s in 'bunkerweb-ui','bunkerweb-api','bunkerweb-scheduler','bunkerweb-nginx') {{ if (Get-Service $s -ErrorAction SilentlyContinue) {{ Stop-Service $s -Force -ErrorAction SilentlyContinue; & ('{#Svc}\' + $s + '.exe') uninstall } }; Get-Process nginx,python,python3 -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like 'C:\bunkerweb\usr\*' } | Stop-Process -Force; Remove-NetFirewallRule -Group 'BunkerWeb' -ErrorAction SilentlyContinue"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveServices"

[UninstallDelete]
Type: filesandordirs; Name: "{#BW}"
Type: filesandordirs; Name: "C:\bunkerweb\tmp\bunkerweb"
Type: filesandordirs; Name: "C:\bunkerweb\var\run\bunkerweb"

[Code]
function RunPS(const Command: String): Integer;
var
  Code: Integer;
begin
  if not Exec(ExpandConstant('{#PS}'), '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Command + '"',
              '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  Log('powershell [' + IntToStr(Code) + ']: ' + Command);
  Result := Code;
end;

function NeedsAddPath(Dir: String): Boolean;
var
  Current: String;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Current) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Current) + ';') = 0;
end;

procedure StopBunkerWeb();
begin
  RunPS('foreach ($s in ''bunkerweb-ui'',''bunkerweb-api'',''bunkerweb-scheduler'',''bunkerweb-nginx'') { ' +
        'if (Get-Service $s -ErrorAction SilentlyContinue) { Stop-Service $s -Force -ErrorAction SilentlyContinue } }; ' +
        'Start-Sleep -Seconds 2; ' +
        'Get-Process nginx,python,python3 -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ''C:\bunkerweb\usr\*'' } | Stop-Process -Force');
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not DirExists('C:\') then
  begin
    MsgBox('BunkerWeb for Windows must be installed on drive C:, which does not exist on this system.', mbCriticalError, MB_OK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopBunkerWeb();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if CurStep <> ssPostInstall then
    exit;

  // configuration and data contain secrets (API tokens, admin password, private keys)
  Exec(ExpandConstant('{sys}\icacls.exe'), '"C:\bunkerweb\etc\bunkerweb" /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\icacls.exe'), '"C:\bunkerweb\var\lib\bunkerweb" /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F', '', SW_HIDE, ewWaitUntilTerminated, Code);

  // register the services (kept as they are on upgrade: WinSW reads its XML on every start)
  RunPS('foreach ($s in ''bunkerweb-nginx'',''bunkerweb-scheduler'',''bunkerweb-ui'',''bunkerweb-api'') { ' +
        'if (-not (Get-Service $s -ErrorAction SilentlyContinue)) { & (''{#Svc}\'' + $s + ''.exe'') install; if ($LASTEXITCODE) { exit 1 } } }');

  if WizardIsTaskSelected('webui') then
    RunPS('if ((Get-Service bunkerweb-ui).StartType -eq ''Disabled'') { Set-Service bunkerweb-ui -StartupType Automatic }')
  else
    RunPS('Set-Service bunkerweb-ui -StartupType Disabled');

  if WizardIsTaskSelected('firewall') then
    RunPS('Remove-NetFirewallRule -Group ''BunkerWeb'' -ErrorAction SilentlyContinue; ' +
          'New-NetFirewallRule -Group ''BunkerWeb'' -DisplayName ''BunkerWeb HTTP/HTTPS'' -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow -Profile Any | Out-Null');

  if WizardIsTaskSelected('startservices') then
  begin
    RunPS('Start-Service bunkerweb-nginx');
    RunPS('Start-Service bunkerweb-scheduler');
    if WizardIsTaskSelected('webui') then
      RunPS('Start-Service bunkerweb-ui');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Current: String;
  P: Integer;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Current) then
  begin
    P := Pos(';C:\bunkerweb\usr\bin', Current);
    if P > 0 then
    begin
      Delete(Current, P, Length(';C:\bunkerweb\usr\bin'));
      RegWriteExpandStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Current);
    end;
  end;
end;
