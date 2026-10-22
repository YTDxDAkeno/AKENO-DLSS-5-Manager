; Inno Setup script for AKENO DLSS 5 Manager.
; Per-user installation (no administrator rights). User data in %LOCALAPPDATA%\AKENO (settings, install
; records and BACKUPS of game files) is deliberately NOT removed on uninstall, so mods installed by AKENO
; can still be restored with a later AKENO installation.

#ifndef AppVersion
  #define AppVersion "1.6.0"
#endif

#ifndef SourceDir
  #define SourceDir "..\dist\AKENO"
#endif
#ifndef ArtifactDir
  #define ArtifactDir "out"
#endif

[Setup]
AppId={{6B1C7E64-5A3E-4D0B-9C2B-3F0A6E1D5C21}
AppName=AKENO DLSS 5 Manager
AppVersion={#AppVersion}
AppPublisher=AKENO community project
AppPublisherURL=https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager
DefaultDirName={localappdata}\Programs\AKENO DLSS 5 Manager
DefaultGroupName=AKENO DLSS 5 Manager
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ArtifactDir}
OutputBaseFilename=AKENO-DLSS5-Manager-{#AppVersion}-Setup-x64
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
Name: "{group}\AKENO DLSS 5 Manager"; Filename: "{app}\AKENO.exe"
Name: "{group}\{cm:UninstallProgram,AKENO DLSS 5 Manager}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AKENO DLSS 5 Manager"; Filename: "{app}\AKENO.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AKENO.exe"; Description: "{cm:LaunchProgram,AKENO DLSS 5 Manager}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the optional "Launch with Windows" entry if the user enabled it.
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v ""AKENO DLSS 5 Manager"" /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"
