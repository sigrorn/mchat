; Inno Setup script for mchat
; Download Inno Setup 6 from https://jrsoftware.org/isdl.php

[Setup]
AppName=mchat
AppVersion=1.0
AppPublisher=mchat
DefaultDirName={autopf}\mchat
DefaultGroupName=mchat
OutputDir=Output
OutputBaseFilename=mchat-setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=src\mchat\resources\icon.ico
UninstallDisplayIcon={app}\mchat.exe
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\mchat\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\mchat"; Filename: "{app}\mchat.exe"; IconFilename: "{app}\mchat.exe"
Name: "{group}\Uninstall mchat"; Filename: "{uninstallexe}"
Name: "{autodesktop}\mchat"; Filename: "{app}\mchat.exe"; IconFilename: "{app}\mchat.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\mchat.exe"; Description: "Launch mchat"; Flags: nowait postinstall skipifsilent
