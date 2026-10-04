; Script Inno Setup - WaseBoard App
; Compilation : ouvrir ce fichier avec Inno Setup Compiler (https://jrsoftware.org/isinfo.php)
; Pré-requis : avoir publié l'app au préalable avec :
;   dotnet publish src/WaseBoard/WaseBoard.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true

#define MyAppName "WaseBoard"
#define MyAppVersion "3.1.0"
; ↑ Pensez à incrémenter ce numéro à chaque nouvelle version distribuée à vos utilisateurs
; (pas obligatoire pour que la mise à jour fonctionne — c'est l'AppId ci-dessous qui compte —
; mais ça permet de distinguer les versions dans le nom du fichier et dans Windows).
#define MyAppPublisher "VotreNom"
#define MyAppExeName "WaseBoard.exe"
#define PublishDir "..\src\WaseBoard\bin\Release\net8.0-windows\win-x64\publish"

[Setup]
AppId={{9F1E3B2A-6C4D-4E7A-9A1B-2C3D4E5F6A7B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=Output
OutputBaseFilename=WaseBoard-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
; --- Mise à jour propre : si WaseBoard est déjà installé (même AppId), l'installeur le
; détecte automatiquement, réutilise le même dossier et les mêmes réglages sans redemander,
; et saute les pages de choix (dossier, groupe) qui n'ont plus lieu d'être sur une mise à jour.
UsePreviousAppDir=yes
UsePreviousGroup=yes
DisableDirPage=auto
DisableProgramGroupPage=auto
; Si WaseBoard.exe est en cours d'exécution au moment de l'installation, le ferme
; automatiquement (Gestionnaire de redémarrage Windows) puis le relance après coup.
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=yes
; Décommentez et configurez la ligne suivante pour signer automatiquement l'installeur
; final lors de la compilation (nécessite d'avoir déclaré "SignTool" dans
; Outils > Options de configuration > Outils de signature, dans Inno Setup) :
; SignTool=signtool /f $qmoncert.pfx$q /p motdepasse /fd sha256 /tr http://timestamp.digicert.com /td sha256 $f

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[Files]
Source: "{#PublishDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Le wildcard ci-dessus copie l'exe ET tout fichier annexe (dll natives, resources, etc.)
; que le mode "fichier unique" n'aurait pas fusionné. Plus sûr que de ne copier que l'.exe.

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Registry]
; Protocole waseboard:// : un lien cliquable (envoyé par un admin, ou via le bouton Discord
; /configurer-invitation) peut pré-remplir l'adresse/le jeton serveur dans l'onboarding —
; voir App.xaml.cs. HKCU (pas HKLM) car PrivilegesRequired=lowest, pas de droits admin.
Root: HKCU; Subkey: "Software\Classes\waseboard"; ValueType: string; ValueName: ""; ValueData: "URL:WaseBoard Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\waseboard"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\waseboard\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent
