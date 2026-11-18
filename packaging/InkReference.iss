#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#ifndef AppSource
  #define AppSource AddBackslash(SourcePath) + "..\release\app\InkReference"
#endif

[Setup]
AppId={{A036368C-8FEC-4E34-B5E7-51A745AC311B}
AppName=InkReference
AppVersion={#AppVersion}
AppPublisher=InkReference
DefaultDirName={localappdata}\Programs\InkReference
DefaultGroupName=InkReference
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=output
OutputBaseFilename=InkReference-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\backend\inkreference\assets\icon.ico
UninstallDisplayIcon={app}\InkReference.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\InkReference"; Filename: "{app}\InkReference.exe"; WorkingDir: "{app}"; AppUserModelID: "com.inkreference.desktop"

[Run]
Filename: "{app}\InkReference.exe"; Description: "Launch InkReference"; Flags: nowait postinstall skipifsilent

; No user data location is registered for deletion. Uninstall removes the
; installed application and shortcuts, preserving LOCALAPPDATA\InkReference.
