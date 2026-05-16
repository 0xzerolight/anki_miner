; Inno Setup script for Anki Miner
; Compile with: iscc /DAppVersion=X.Y.Z anki_miner.iss

#ifndef AppVersion
  #define AppVersion "dev"
#endif

[Setup]
AppId={{15B09250-AC39-4792-A15A-B73BD8E218A1}
AppName=Anki Miner
AppVersion={#AppVersion}
AppVerName=Anki Miner {#AppVersion}
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
