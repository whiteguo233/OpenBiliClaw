; Inno Setup script for the OpenBiliClaw Windows installer.
;
; Compile on Windows (Inno Setup 6):
;     iscc /DMyAppVersion=0.3.101 packaging\openbiliclaw.iss
; Produces:
;     dist\release\OpenBiliClaw-windows-0.3.101-Setup.exe
;
; Expects the PyInstaller onedir output at dist\OpenBiliClaw\ with a bundled
; ollama.exe + lib\ runners already staged inside it. The GitHub Actions
; workflow (.github/workflows/build-installers.yml) produces that layout; to
; build locally, run `python packaging\build.py` then stage ollama into
; dist\OpenBiliClaw\ before invoking iscc.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "OpenBiliClaw"
#define MyAppPublisher "OpenBiliClaw Contributors"
#define MyAppURL "https://github.com/whiteguo233/OpenBiliClaw"
#define MyAppExeName "OpenBiliClaw.exe"

[Setup]
; A stable AppId keeps upgrades/uninstall coherent across versions — do not change.
AppId={{B4F3D2A1-7C6E-4A8B-9D1F-0E2A6C5B3D14}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install → no admin rights, no UAC prompt. The app is unsigned, so
; this keeps install friction as low as possible (SmartScreen may still warn).
PrivilegesRequired=lowest
; Upgrades fail with "files in use" if the previous OpenBiliClaw is still
; running (it holds OpenBiliClaw.exe + the bundled ollama it spawned open).
; Force the Restart Manager to close anything holding our files, and the [Code]
; below also taskkills the process tree as a belt-and-suspenders fallback
; (PyInstaller console apps don't always cooperate with RM).
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Script lives in packaging\; resolve [Files] Source + OutputDir from repo root.
SourceDir=..
OutputDir=dist\release
OutputBaseFilename=OpenBiliClaw-windows-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Whole PyInstaller onedir tree, including the staged ollama.exe + lib\ runners.
Source: "dist\OpenBiliClaw\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; NOTE: user data (config.toml, data\, logs\) lives under %LOCALAPPDATA%\OpenBiliClaw,
; NOT under {app} — see packaging/entry.py (_user_data_root). Keeping it out of the
; install dir means upgrades never lock the database and uninstall never touches the
; user's profile. The app migrates data left in {app} by older builds on first run.

[Code]
procedure StopRunningInstance;
var
  ResultCode: Integer;
begin
  // Best-effort: terminate any running OpenBiliClaw (and its child processes —
  // the backend, and the bundled ollama it may have spawned) so their open file
  // handles release before Setup overwrites {app}. taskkill is a no-op (nonzero
  // exit, ignored) when nothing is running.
  Exec(ExpandConstant('{cmd}'), '/C taskkill /IM "{#MyAppExeName}" /T /F', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // Give Windows a moment to release the handles before the file copy begins.
  Sleep(800);
end;

// Runs right before files are copied (both fresh installs and upgrades).
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningInstance;
  Result := '';
end;
