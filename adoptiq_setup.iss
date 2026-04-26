; AdoptIQ Windows Installer - Inno Setup script
; Run: iscc adoptiq_setup.iss (requires Inno Setup 6)
; Output: AdoptIQ-Setup.exe in OUTBOX

#define MyAppName "AdoptIQ"
#define MyAppVersion "1.0"
#define MyAppPublisher "AdoptIQ"
#define MyAppURL "http://localhost:5151"
#define MyAppExeName "AdoptIQ.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={userappdata}\AdoptIQ
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=OUTBOX
OutputBaseFilename=AdoptIQ-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; DestName: "README.md"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Start AdoptIQ server"
Name: "{group}\README"; Filename: "{app}\README.md"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "Start AdoptIQ server"

[Run]
Filename: "{app}\README.md"; Description: "View README"; Flags: postinstall shellexec skipifsilent
Filename: "{app}\{#MyAppExeName}"; Description: "Launch AdoptIQ now"; Flags: postinstall nowait skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    ForceDirectories(ExpandConstant('{app}\uploads'));
    ForceDirectories(ExpandConstant('{app}\outputs'));
  end;
end;
