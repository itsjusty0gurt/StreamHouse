#define AppName "Streamhouse Hub"
#define AppVersion "0.2.1-alpha"
#define AppNumericVersion "0.2.1.0"
#define AppInstallerBasename "StreamhouseHub-0.2.1-alpha-Setup"
#define AppExeName "StreamhouseHub.exe"

[Setup]
AppId={{B16DA343-4773-4819-B505-65911DA0675B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Streamhouse
AppSupportURL=https://github.com/itsjusty0gurt/StreamHouse/issues
AppUpdatesURL=https://github.com/itsjusty0gurt/StreamHouse/releases
DefaultDirName={autopf}\Streamhouse\Streamhouse Hub
DefaultGroupName=Streamhouse
DisableDirPage=auto
DisableProgramGroupPage=auto
AllowNoIcons=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no
SetupLogging=yes
UninstallLogging=yes
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
OutputDir=..\..\release
OutputBaseFilename={#AppInstallerBasename}
SetupIconFile=..\..\shared\assets\streamhouse-icons\windows\streamhouse-hub.ico
VersionInfoCompany=Streamhouse
VersionInfoDescription=Streamhouse Hub Alpha 0.2.1 Installer
VersionInfoOriginalFileName={#AppInstallerBasename}.exe
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppNumericVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoVersion={#AppNumericVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\StreamhouseHub\*"; DestDir: "{app}"; Excludes: "_internal\extensions\twitch\app\*.py,_internal\extensions\twitch\app\__pycache__\*,_internal\extensions\twitch\app\README.md,_internal\extensions\twitch\app\listing\*"; Flags: ignoreversion recursesubdirs createallsubdirs restartreplace

[Icons]
Name: "{group}\Streamhouse Hub"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Streamhouse Hub"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch Streamhouse Hub"; Flags: nowait postinstall skipifsilent
