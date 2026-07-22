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
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Always capture installer diagnostics in the user's TEMP directory.
SetupLogging=yes
; Prevent concurrent installer instances from racing.
SetupMutex=AnkiMinerSetup-15B09250-AC39-4792-A15A-B73BD8E218A1
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[InstallDelete]
; Wipe all orphan dist-info dirs from prior installs before [Files] copies the
; new ones. Inno overlay installs (Flags: ignoreversion) leave version-suffixed
; dirs from older versions next to the new ones; importlib.metadata.version()
; enumerates dist-info by filesystem order and can return the older entry.
; Issue #10 hit anki_miner directly; the broader pattern protects every dep
; (PyQt6, requests, fugashi, pysubs2, packaging, psutil, yt_dlp, ...) from the
; same trap if any of them — or future app code — calls importlib.metadata.
Type: filesandordirs; Name: "{app}\_internal\*.dist-info"

[Files]
Source: "..\..\dist\AnkiMiner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Anki Miner"; Filename: "{app}\AnkiMiner.exe"
Name: "{group}\Uninstall Anki Miner"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Anki Miner"; Filename: "{app}\AnkiMiner.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AnkiMiner.exe"; Description: "Launch Anki Miner"; Flags: nowait postinstall skipifsilent
