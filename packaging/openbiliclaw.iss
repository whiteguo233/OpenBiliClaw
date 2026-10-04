; Inno Setup script for the OpenBiliClaw Windows installer.
;
; Compile on Windows (Inno Setup 6):
;     iscc /DMyAppVersion=0.3.226 packaging\openbiliclaw.iss
; Produces:
;     dist\release\OpenBiliClaw-windows-0.3.226-Setup.exe
;
; Expects the PyInstaller onedir output at dist\OpenBiliClaw\ with a bundled
; ollama.exe + lib\ runners already staged inside it. The GitHub Actions
; workflow (.github/workflows/build-installers.yml) produces that layout; to
; build locally, run `python packaging\build.py` then stage ollama into
; dist\OpenBiliClaw\ before invoking iscc.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#ifndef MyAppVersionInfoVersion
  #define MyAppVersionInfoVersion MyAppVersion
#endif

; Installer filename variant suffix, e.g. iscc /DMyAppVariantSuffix=-with-embedding
; Lets the lean and with-embedding installers coexist in one Release without
; clobbering each other. Defaults to empty (lean).
#ifndef MyAppVariantSuffix
  #define MyAppVariantSuffix ""
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
VersionInfoVersion={#MyAppVersionInfoVersion}
VersionInfoProductVersion={#MyAppVersionInfoVersion}
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
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
; Setup AND Uninstall open with the standard "application is running" dialog
; when the packaged app holds the named mutex created by packaging/entry.py
; (_acquire_installer_mutex). CloseApplications/Restart Manager above is
; install-only, and the uninstaller treats locked-file delete errors as
; non-fatal — without this gate it stranded the locked files, deleted itself,
; and left no way to retry the uninstall. Older installed builds without the
; mutex fall through to the CurUninstallStepChanged(usUninstall) taskkill in
; [Code] (deliberately AFTER the gate — see the ordering note there).
AppMutex=OpenBiliClaw-B4F3D2A1-7C6E-4A8B-9D1F-0E2A6C5B3D14
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Script lives in packaging\; resolve [Files] Source + OutputDir from repo root.
SourceDir=..
OutputDir=dist\release
OutputBaseFilename=OpenBiliClaw-windows-{#MyAppVersion}{#MyAppVariantSuffix}-Setup
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
; Interactive installs: a checked "Launch OpenBiliClaw" checkbox on the Finish
; page. The app starts only when the user clicks Finish — never while the
; wizard is still open — and the user may uncheck it to not launch at all.
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: postinstall nowait skipifsilent
; Silent installs/upgrades (/SILENT, /VERYSILENT): no Finish page is shown, but
; the postinstall entry above WOULD still run — the wizard auto-clicks through
; the hidden Finished page — so skipifsilent holds it back and this mirror entry
; takes over. PrepareToInstall stopped the old process tree, so a successful
; silent setup must hand off to the freshly written {app} binary instead of
; leaving nothing running (the upgrade regression that originally forced an
; unconditional launch here). Both skip flags are load-bearing: an unscoped
; entry would launch the app twice on a silent install.
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Flags: nowait skipifnotsilent

; NOTE: user data (config.toml, data\, logs\) lives under
; %USERPROFILE%\OpenBiliClaw, the same root used by the one-line / AI installers,
; NOT under {app} — see packaging/entry.py (_user_data_root). Keeping it out of the
; install dir means upgrades never lock the database and uninstall never touches the
; user's profile. The app migrates data left in {app}, and copies data from the
; older %LOCALAPPDATA%\OpenBiliClaw packaged-app root, on first run.

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

// Uninstall-side process handoff. PrepareToInstall above only runs in Setup,
// and Uninstall cannot use Restart Manager (CloseApplications is install-only),
// so without this the uninstaller hits "file in use" errors — which are
// non-fatal there: it deletes itself and strands the leftovers.
//
// ORDERING IS LOAD-BEARING: Inno runs [Code] InitializeUninstall BEFORE its
// internal AppMutex check (see RunSecondPhase in Setup.Uninstall.pas), so
// killing the app there silently destroys the mutex and the "application is
// running" gate never fires. The kill therefore lives in
// CurUninstallStepChanged(usUninstall), which runs AFTER the AppMutex gate
// passed and immediately before file deletion — cleaning up processes the
// gate cannot see (mutex-less pre-AppMutex installs, orphaned worker/ollama
// children outliving the tray parent). taskkill is a no-op (nonzero exit,
// ignored) when nothing is running.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopRunningInstance;
end;

// Runs right before files are copied (both fresh installs and upgrades).
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningInstance;
  Result := '';
end;
