#define MyAppVersion "0.4.3"
; Build with `iscc /DLean packaging/windows.iss` for the lean installer (no model files); the default is the
; standard installer, which packages dist\ByAuthorX-standard (see scripts/build.py --variant standard).
#ifdef Lean
  #define Edition "-lean"
  #define FrozenDir "..\dist\ByAuthorX"
#else
  #define Edition ""
  #define FrozenDir "..\dist\ByAuthorX-standard"
#endif
[Setup]
AppId={{405848A2-B0A7-41AD-9879-BA490F248A52}
AppName=ByAuthorX
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\ByAuthorX
DefaultGroupName=ByAuthorX
OutputDir=..\dist
OutputBaseFilename=ByAuthorX-{#MyAppVersion}-windows-x64{#Edition}-setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
PrivilegesRequired=lowest
ChangesAssociations=yes
ChangesEnvironment=yes
Compression=lzma2
SolidCompression=yes
LicenseFile=..\LICENSE
SetupIconFile=..\src\byax\resources\brand\byauthorx.ico
UninstallDisplayIcon={app}\ByAuthorX.exe
[Files]
Source: "{#FrozenDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\ByAuthorX"; Filename: "{app}\ByAuthorX.exe"
[Registry]
Root: HKA; Subkey: "Software\Classes\.byax"; ValueType: string; ValueData: "ByAuthorX.Document"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\ByAuthorX.Document"; ValueType: string; ValueData: "ByAuthorX Document"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\ByAuthorX.Document\DefaultIcon"; ValueType: string; ValueData: """{app}\ByAuthorX.exe"",0"
Root: HKA; Subkey: "Software\Classes\ByAuthorX.Document\shell\open\command"; ValueType: string; ValueData: """{app}\ByAuthorX.exe"" ""%1"""
; Listing the app under Applications and OpenWithProgids makes it appear in "Open with" for these types
; without taking over the current default.
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "ByAuthorX"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\shell\open\command"; ValueType: string; ValueData: """{app}\ByAuthorX.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".byax"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".docx"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".doc"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".odt"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".rtf"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".html"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".htm"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".epub"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".md"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".markdown"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\ByAuthorX.exe\SupportedTypes"; ValueType: string; ValueName: ".txt"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\.docx\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.doc\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.odt\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.rtf\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.html\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.htm\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.epub\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.md\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.markdown\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.txt\OpenWithProgids"; ValueType: string; ValueName: "ByAuthorX.Document"; ValueData: ""; Flags: uninsdeletevalue
[Run]
Filename: "{app}\ByAuthorX.exe"; Description: "Open ByAuthorX"; Flags: postinstall nowait skipifsilent

[Code]
procedure UpdateUserPath(AddPath: Boolean);
var
  Existing, AppPath, NewPath: string;
  Entries: TArrayOfString;
  I: Integer;
  Found: Boolean;
begin
  AppPath := ExpandConstant('{app}');
  RegQueryStringValue(HKCU, 'Environment', 'Path', Existing);
  Entries := StringSplit(Existing, [';'], stExcludeEmpty);
  NewPath := '';
  Found := False;
  for I := 0 to GetArrayLength(Entries) - 1 do begin
    if CompareText(Entries[I], AppPath) = 0 then begin
      Found := True;
      if not AddPath then Continue;
    end;
    if NewPath <> '' then NewPath := NewPath + ';';
    NewPath := NewPath + Entries[I];
  end;
  if AddPath and not Found then begin
    if NewPath <> '' then NewPath := NewPath + ';';
    NewPath := NewPath + AppPath;
  end;
  if NewPath <> Existing then
    RegWriteExpandStringValue(HKCU, 'Environment', 'Path', NewPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then UpdateUserPath(True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then UpdateUserPath(False);
end;
