; Inno Setup script for Anki Miner
; Compile with: iscc /DAppVersion=X.Y.Z anki_miner.iss

#ifndef AppVersion
  #define AppVersion "dev"
#endif

; Keep PE version metadata numeric; preserve a valid prefix and zero-pad it.
; Known limitation (accepted): a non-numeric component zeroes itself and stops
; the parse ("2.9.3rc1" -> 2.9.0.0), and >4 components reset to 0.0.0.0. The
; release pipeline only ever passes plain X.Y.Z (release.yml validates the tag
; against __version__) or the "dev" default, both of which expand correctly.
#define PopNumericVersionPart(str *Tail) \
  Local[0] = Pos(".", Tail), \
  Local[1] = Local[0] ? Copy(Tail, 1, Local[0] - 1) : Tail, \
  Local[2] = Int(Local[1], -1), \
  Local[3] = (Local[1] != "") && (Local[2] >= 0) && (Local[2] <= 65535), \
  Tail = (Local[3] && Local[0]) ? Copy(Tail, Local[0] + 1) : "", \
  Local[3] ? Str(Local[2]) : "0"
#define VersionTail Str(AppVersion)
#define VersionPart1 PopNumericVersionPart(VersionTail)
#define VersionPart2 PopNumericVersionPart(VersionTail)
#define VersionPart3 PopNumericVersionPart(VersionTail)
#define VersionPart4 PopNumericVersionPart(VersionTail)
#define NumericVersionPrefix \
  VersionPart1 + "." + VersionPart2 + "." + VersionPart3 + "." + VersionPart4
#define NumericAppVersion (VersionTail == "") ? NumericVersionPrefix : "0.0.0.0"

[Setup]
AppId={{15B09250-AC39-4792-A15A-B73BD8E218A1}
AppName=Anki Miner
AppVersion={#AppVersion}
AppVerName=Anki Miner {#AppVersion}
; Set Setup.exe's binary version from numeric components only.
VersionInfoVersion={#NumericAppVersion}
AppPublisher=Anki Miner Contributors
AppPublisherURL=https://github.com/0xzerolight/anki_miner
DefaultDirName={autopf}\AnkiMiner
DefaultGroupName=Anki Miner
UninstallDisplayIcon={app}\AnkiMiner.exe
OutputDir=..\..\dist
OutputBaseFilename=AnkiMiner-{#AppVersion}-Windows-x86_64-Setup
SetupIconFile=..\..\anki_miner\gui\resources\icons\anki_miner.ico
LicenseFile=..\..\LICENSE
#ifdef ProbeBuild
; CI downgrade-probe build only, never shipped.
Compression=none
SolidCompression=no
#else
Compression=lzma2/ultra64
SolidCompression=yes
#endif
WizardStyle=modern
; Always capture installer diagnostics in the user's TEMP directory.
SetupLogging=yes
; Prevent concurrent installer instances from racing.
SetupMutex=AnkiMinerSetup-15B09250-AC39-4792-A15A-B73BD8E218A1
; Blocks Setup and Uninstall while the app runs; gui/launch.py creates this mutex, so names must stay in sync.
AppMutex=Local\AnkiMiner-15B09250-AC39-4792-A15A-B73BD8E218A1
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; The app ships 12 UI languages; Inno bundles official .isl files for seven of
; them, and picks the user's system language automatically. id, vi, zh_cn and
; zh_tw have no official Inno translation and fall back to English.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Issue #10 showed why stale dist-info directories must not survive overlay
; upgrades: importlib.metadata could enumerate the old version first.
; _internal is installer-owned: it is the PyInstaller onedir runtime. Overlay
; upgrades must not retain obsolete .pyd, .dll, Qt-plugin, or data files absent
; from the new build. Users must never store files in _internal.
; InstallDelete runs before [Files] and is non-transactional. Accepted risk: a
; failed install is recovered by re-running the installer. Never touch {app} root.
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
; App-created (Tools -> Create Desktop Shortcut) and legacy pre-f3711c4a
; shortcut locations outside Inno's install log; exact paths only, never wildcards.
Type: files; Name: "{autodesktop}\Anki Miner.lnk"
Type: files; Name: "{userprograms}\Anki Miner.lnk"
Type: files; Name: "{%USERPROFILE}\Anki Miner.lnk"

[Files]
Source: "..\..\dist\AnkiMiner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Anki Miner"; Filename: "{app}\AnkiMiner.exe"
Name: "{group}\{cm:UninstallProgram,Anki Miner}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Anki Miner"; Filename: "{app}\AnkiMiner.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AnkiMiner.exe"; Description: "{cm:LaunchProgram,Anki Miner}"; Flags: nowait postinstall skipifsilent
; An update the app started (/UPDATE=1, anki_miner/services/app_updater.py) runs
; silently, so the entry above is skipped; relaunch the app here instead.
Filename: "{app}\AnkiMiner.exe"; Flags: nowait; Check: IsUpdateMode

[Code]
// True when the app's in-place updater started this Setup.
function IsUpdateMode: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

// The app starts this installer and then exits, so in update mode its AppMutex
// can still be held for a moment. Setup checks AppMutex right AFTER this
// function returns (issrc Setup.MainFunc.pas) and, under /SUPPRESSMSGBOXES,
// answers its OK/Cancel box with Cancel and aborts. Wait here first. Bounded:
// if the app is still running after 60 s, the normal AppMutex prompt follows.
// The name must match AppMutex above and gui/launch.py APP_MUTEX_NAME.
function InitializeSetup: Boolean;
var
  WaitedMs: Integer;
begin
  Result := True;
  if not IsUpdateMode then
    Exit;
  WaitedMs := 0;
  while CheckForMutexes('Local\AnkiMiner-15B09250-AC39-4792-A15A-B73BD8E218A1') and (WaitedMs < 60000) do
  begin
    Sleep(250);
    WaitedMs := WaitedMs + 250;
  end;
  // Read by the installer smoke (scripts/windows_installer_smoke_lib.ps1) and
  // by whoever reads a user's Setup log.
  Log(Format('Update mode: waited %d ms for Anki Miner to exit', [WaitedMs]));
end;

// A nonempty result blocks a downgrade at PrepareToInstall (Setup exit code 7).
// GetPackedVersion failure (missing/damaged AnkiMiner.exe) deliberately fails
// open so rerunning any installer can repair a broken installation; this guard
// only blocks verifiable downgrades.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Incoming, Installed: Int64;
begin
  Result := '';
  Incoming := PackVersionComponents(
    {#VersionPart1}, {#VersionPart2}, {#VersionPart3}, {#VersionPart4});
  if Incoming = 0 then
    Exit;
  if ExpandConstant('{param:ALLOWDOWNGRADE|0}') = '1' then
    Exit;
  if GetPackedVersion(ExpandConstant('{app}\AnkiMiner.exe'), Installed) and
     (ComparePackedVersion(Installed, Incoming) > 0) then
  begin
    Result :=
      'A newer version of Anki Miner is installed. Downgrading is not supported ' +
      'because newer settings and dictionary indexes are not backward compatible. ' +
      'Rerun Setup with /ALLOWDOWNGRADE=1 to override (not recommended).';
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
    MsgBox(
      'Anki Miner user data (settings, dictionaries, models, caches, and databases) ' +
      'was kept at %USERPROFILE%\.anki_miner and can be removed manually.',
      mbInformation,
      MB_OK);
end;
