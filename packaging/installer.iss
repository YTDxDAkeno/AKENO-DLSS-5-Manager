; Inno Setup script for AKENO Gaming Suite (the same AppId as AKENO DLSS 5 Manager: it upgrades 2.x in place).
; Per-user installation (no administrator rights). User data in %LOCALAPPDATA%\AKENO (settings, install
; records and BACKUPS of game files) is deliberately NOT removed on uninstall, so mods installed by AKENO
; can still be restored with a later AKENO installation.

#ifndef AppVersion
  #define AppVersion "3.0.0"
#endif

#ifndef SourceDir
  #define SourceDir "..\dist\AKENO"
#endif
#ifndef ArtifactDir
  #define ArtifactDir "out"
#endif

[Setup]
AppId={{6B1C7E64-5A3E-4D0B-9C2B-3F0A6E1D5C21}
AppName=AKENO Gaming Suite
AppVersion={#AppVersion}
AppPublisher=AKENO community project
AppPublisherURL=https://akeno.online
AppSupportURL=https://akeno.online
AppUpdatesURL=https://akeno.online
; AKENO must not run while its files are replaced (the in-app updater handles running copies itself)
CloseApplications=yes
RestartApplications=no
DefaultDirName={localappdata}\Programs\AKENO Gaming Suite
DefaultGroupName=AKENO Gaming Suite
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ArtifactDir}
OutputBaseFilename=AKENO-Gaming-Suite-{#AppVersion}-Setup-x64
SetupIconFile=akeno.ico
UninstallDisplayIcon={app}\AKENO.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\AKENO Gaming Suite"; Filename: "{app}\AKENO.exe"
Name: "{group}\{cm:UninstallProgram,AKENO Gaming Suite}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AKENO Gaming Suite"; Filename: "{app}\AKENO.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AKENO.exe"; Description: "{cm:LaunchProgram,AKENO Gaming Suite}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the optional "Launch with Windows" entry if the user enabled it.
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v ""AKENO DLSS 5 Manager"" /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"

[UninstallDelete]
; The in-app updater replaces the program folder (it keeps the previous version in %LOCALAPPDATA%\AKENO\updates),
; so files of later versions are removed too. User data in %LOCALAPPDATA%\AKENO is not in this folder.
Type: filesandordirs; Name: "{app}"
