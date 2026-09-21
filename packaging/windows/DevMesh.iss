#define AppVersion GetEnv("DEVMESH_RELEASE_VERSION")
#define SourceDir GetEnv("DEVMESH_WINDOWS_DIST")
#define OutputDir GetEnv("DEVMESH_RELEASE_DIR")

[Setup]
AppId={{B374CF36-735E-48CB-96F1-8B393D8EE36F}
AppName=DevMesh Studio
AppVersion={#AppVersion}
AppPublisher=DevMesh
DefaultDirName={localappdata}\Programs\DevMesh Studio
DefaultGroupName=DevMesh Studio
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputDir={#OutputDir}
OutputBaseFilename=DevMesh-Studio-{#AppVersion}-Windows-x86_64-Setup
UninstallDisplayIcon={app}\DevMesh Studio.exe

[Files]
Source: "{#SourceDir}\DevMesh Studio.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\devmesh-server.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\DevMesh Studio"; Filename: "{app}\DevMesh Studio.exe"
Name: "{autodesktop}\DevMesh Studio"; Filename: "{app}\DevMesh Studio.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\DevMesh Studio.exe"; Description: "Launch DevMesh Studio"; Flags: nowait postinstall skipifsilent
