<#
.SYNOPSIS
  Récupère sur ce PC la dernière sauvegarde du serveur WaseBoard (sauvegarde HORS de la machine serveur).

.DESCRIPTION
  Le serveur écrit chaque nuit une archive de ses données (waseboard-data-*.tgz) et, avec `backup.sh -c`, une archive
  séparée de ses secrets (waseboard-config-*.tgz : jeton du bot, secret partagé, sessions Discord). Ces archives sont sur la
  MÊME machine que le serveur : si elle tombe en panne, elles partent avec elle. Ce script les copie ici par SSH.

  Pour chaque archive : copie dans un fichier .part, contrôle de la taille puis du contenu (`tar -tzf` : l'archive se relit en
  entier et contient le catalogue / config.json), seulement ensuite renommage ; les plus anciennes sont supprimées en local
  (-Keep). Une archive déjà récupérée n'est pas recopiée. Rien n'est jamais modifié ni supprimé sur le serveur.

  Prérequis : Windows 10 ou plus (ssh.exe, scp.exe et tar.exe sont fournis), et une clé SSH qui ouvre une session sur le serveur
  sans mot de passe (la même que pour administrer la machine). Lancez le script une première fois à la main : SSH vous demande
  de confirmer l'empreinte du serveur (« yes »), puis plus rien.

  Les archives de réglages contiennent des SECRETS : le dossier de destination est réservé à votre compte Windows. Ne les
  copiez ni dans un dépôt ni sur un service en ligne ; -NoSecrets les laisse sur le serveur.

.PARAMETER Server     Adresse (ou nom) de la machine serveur.
.PARAMETER Port       Port SSH (défaut 22).
.PARAMETER User       Compte SSH sur le serveur.
.PARAMETER KeyFile    Fichier de la clé privée SSH (défaut : ~/.ssh/id_ed25519).
.PARAMETER RemoteDir  Dossier des sauvegardes sur le serveur (défaut : ~/waseboard-backups ; instance de test : ~/waseboard-backups-test).
.PARAMETER LocalDir   Dossier de destination ici (défaut : %USERPROFILE%\WaseBoard-sauvegardes).
.PARAMETER Keep       Nombre d'archives gardées ici, par sorte (défaut 14).
.PARAMETER NoSecrets  Ne récupère pas l'archive des réglages (secrets).
.PARAMETER Install    Crée la tâche planifiée « WaseBoard - sauvegarde hors machine » (une fois par jour) avec ces mêmes paramètres.
.PARAMETER Uninstall  Supprime cette tâche planifiée.
.PARAMETER At         Heure de la tâche planifiée (défaut 12:00). Si le PC était éteint à ce moment, elle part dès que possible.

.EXAMPLE
  .\pull-backup.ps1 -Server 192.168.1.10 -Port 22 -User moi -KeyFile $HOME\.ssh\waseboard_server
  .\pull-backup.ps1 -Server 192.168.1.10 -User moi -KeyFile $HOME\.ssh\waseboard_server -Install
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Server,
    [int]$Port = 22,
    [Parameter(Mandatory)][string]$User,
    [string]$KeyFile = (Join-Path $HOME '.ssh\id_ed25519'),
    [string]$RemoteDir = '~/waseboard-backups',
    [string]$LocalDir = (Join-Path $env:USERPROFILE 'WaseBoard-sauvegardes'),
    [ValidateRange(1, 3650)][int]$Keep = 14,
    [switch]$NoSecrets,
    [switch]$Install,
    [switch]$Uninstall,
    [string]$At = '12:00'
)

$ErrorActionPreference = 'Stop'
$taskName = 'WaseBoard - sauvegarde hors machine'

# ---------- Tâche planifiée ----------
if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Tâche « $taskName » supprimée."
    } else {
        Write-Host "Aucune tâche « $taskName »."
    }
    return
}
if ($Install) {
    $arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Server `"$Server`" -Port $Port -User `"$User`" " +
        "-KeyFile `"$KeyFile`" -RemoteDir `"$RemoteDir`" -LocalDir `"$LocalDir`" -Keep $Keep"
    if ($NoSecrets) { $arguments += ' -NoSecrets' }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -User $env:USERNAME -RunLevel Limited `
        -Description 'Copie la dernière sauvegarde du serveur WaseBoard sur ce PC (tools/pull-backup.ps1).' -Force | Out-Null
    Write-Host "Tâche « $taskName » créée : tous les jours à $At (ou dès que le PC est allumé, si l'heure est passée)."
    Write-Host "Journal : $(Join-Path $LocalDir 'pull-backup.log')"
    return
}

# ---------- Récupération ----------
New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
$logFile = Join-Path $LocalDir 'pull-backup.log'
function Log([string]$message, [string]$level = 'INFO') {
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $level, $message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    if ($level -eq 'ERREUR') { Write-Host $line -ForegroundColor Red } else { Write-Host $line }
}

# Dossier réservé à ce compte Windows (les archives de réglages contiennent des secrets).
try { & icacls $LocalDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null } catch { }

if (-not (Test-Path $KeyFile)) { Log "clé SSH introuvable : $KeyFile" 'ERREUR'; exit 2 }
# ServerAlive : une connexion qui ne répond plus est abandonnée au bout d'une minute environ (sans cela un serveur muet bloquerait le script).
$sshOptions = @('-i', $KeyFile, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=15',
    '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4')

function Invoke-Remote([string]$command) {
    # -n : ssh ne lit pas l'entrée standard. Sans cela, dans la fenêtre de la tâche planifiée, le 2e appel ssh d'un même lancement attendait
    # indéfiniment la console (la commande distante était finie, la fenêtre restait ouverte jusqu'à l'arrêt forcé au bout d'une heure).
    $output = & ssh.exe -n @sshOptions -p $Port "$User@$Server" $command 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ssh a échoué (code $LASTEXITCODE) : $output" }
    return ($output | Out-String).Trim()
}

function Test-Archive([string]$path, [string]$mustContain) {
    $listing = & tar.exe -tzf $path 2>&1
    if ($LASTEXITCODE -ne 0) { return "l'archive est illisible" }
    if (-not ($listing -contains $mustContain)) { return "$mustContain manque dans l'archive" }
    return $null
}

function Pull-Kind([string]$kind, [string]$mustContain, [bool]$required) {
    $latest = Invoke-Remote "cd $RemoteDir && ls -1t waseboard-$kind-*.tgz 2>/dev/null | head -n 1"
    if (-not $latest) {
        if ($required) { throw "aucune archive waseboard-$kind-*.tgz dans $RemoteDir sur le serveur" }
        Log "pas d'archive « $kind » sur le serveur (lancez backup.sh avec -c pour avoir celle des réglages)."
        return
    }
    $remoteSize = [int64](Invoke-Remote "stat -c %s $RemoteDir/$latest")
    $final = Join-Path $LocalDir $latest
    if ((Test-Path $final) -and (Get-Item $final).Length -eq $remoteSize) {
        Log "$latest : déjà récupérée."
    } else {
        $part = "$final.part"
        if (Test-Path $part) { Remove-Item $part -Force }
        & scp.exe @sshOptions -P $Port "${User}@${Server}:$RemoteDir/$latest" $part 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Remove-Item $part -Force -ErrorAction SilentlyContinue; throw "scp a échoué pour $latest (code $LASTEXITCODE)" }
        if ((Get-Item $part).Length -ne $remoteSize) { Remove-Item $part -Force; throw "$latest : taille reçue différente de celle du serveur" }
        $problem = Test-Archive $part $mustContain
        if ($problem) { Remove-Item $part -Force; throw "$latest écartée : $problem" }
        Move-Item $part $final -Force
        Log ("{0} : récupérée ({1:N1} Mo, contenu vérifié)." -f $latest, ($remoteSize / 1MB))
    }
    # Rotation locale : on garde les $Keep plus récentes de cette sorte.
    $old = Get-ChildItem $LocalDir -Filter "waseboard-$kind-*.tgz" | Sort-Object Name -Descending | Select-Object -Skip $Keep
    foreach ($file in $old) { Remove-Item $file.FullName -Force; Log "$($file.Name) : supprimée (au-delà des $Keep gardées)." }
}

try {
    Pull-Kind 'data' 'sounds_data/catalog.json' $true
    if (-not $NoSecrets) { Pull-Kind 'config' 'config.json' $false }
    Log 'sauvegarde hors machine à jour.'
    exit 0
} catch {
    Log $_.Exception.Message 'ERREUR'
    exit 1
}
