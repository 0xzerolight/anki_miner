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
; Wipe orphan version-suffixed paths from prior installs before laying down new files.
; Without this, dist-info dirs from older installs (anki_miner-2.3.3.dist-info)
; coexist with the new ones and earlier-named entries can win filesystem-order
; lookups by importlib.metadata. See Issue #10.
Type: filesandordirs; Name: "{app}\_internal\anki_miner-*.dist-info"
Type: filesandordirs; Name: "{app}\_internal\yt_dlp-*.dist-info"

[Files]
Source: "..\..\dist\AnkiMiner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Anki Miner"; Filename: "{app}\AnkiMiner.exe"
Name: "{group}\Uninstall Anki Miner"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Anki Miner"; Filename: "{app}\AnkiMiner.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AnkiMiner.exe"; Description: "Launch Anki Miner"; Flags: nowait postinstall skipifsilent
