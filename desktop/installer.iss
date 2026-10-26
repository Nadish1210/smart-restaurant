; Inno Setup script - builds SmartRestaurant_Setup.exe from the PyInstaller output
[Setup]
AppName=Smart Restaurant
AppVersion=1.0.0
AppPublisher=Nadish
AppPublisherURL=https://smartrestaurant1210.streamlit.app/
DefaultDirName={autopf}\Smart Restaurant
DefaultGroupName=Smart Restaurant
OutputBaseFilename=SmartRestaurant_Setup
OutputDir=Output
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\SmartRestaurant.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: checkedonce

[Files]
Source: "dist\SmartRestaurant\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Smart Restaurant"; Filename: "{app}\SmartRestaurant.exe"
Name: "{autodesktop}\Smart Restaurant"; Filename: "{app}\SmartRestaurant.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SmartRestaurant.exe"; Description: "Launch Smart Restaurant"; Flags: nowait postinstall skipifsilent
