$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$MonitorScript = Join-Path $ScriptDir "watchfrog.py"
$ConfigRoot = Join-Path $env:APPDATA "WatchFrog"
$ConfigFile = Join-Path $ConfigRoot "config.toml"
$TaskName = "WatchFrog"

Write-Host ""
Write-Host "WatchFrog – Windows-Einrichtung"
Write-Host "==============================="
Write-Host ""

$Python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = (
        & py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 11)); print(sys.executable)" 2>$null
    )
}
if (-not $Python -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $Candidate = (Get-Command python).Source
    & $Candidate -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
    if ($LASTEXITCODE -eq 0) {
        $Python = $Candidate
    }
}
if (-not $Python) {
    throw "Benötigt wird Python 3.11 oder neuer."
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg wurde nicht gefunden. Installation z.B. mit: winget install Gyan.FFmpeg"
}

& $Python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Berlin')" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installiere die Zeitzonendaten für Windows …"
    & $Python -m pip install --user tzdata
    if ($LASTEXITCODE -ne 0) {
        throw "Die Windows-Zeitzonendaten konnten nicht installiert werden."
    }
}

New-Item -ItemType Directory -Force -Path $ConfigRoot | Out-Null
if (-not (Test-Path $ConfigFile)) {
    & $Python $MonitorScript --configure --config $ConfigFile
    if ($LASTEXITCODE -ne 0) { throw "Die Konfiguration konnte nicht erstellt werden." }
}

& $Python $MonitorScript --check --config $ConfigFile
if ($LASTEXITCODE -ne 0) { throw "Die Konfigurationsprüfung ist fehlgeschlagen." }
& $Python $MonitorScript --test-telegram --config $ConfigFile
if ($LASTEXITCODE -ne 0) { throw "Der Telegram-Test ist fehlgeschlagen." }
& $Python $MonitorScript --test-healthcheck --config $ConfigFile
if ($LASTEXITCODE -ne 0) { throw "Der Healthchecks.io-Test ist fehlgeschlagen." }

$Pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
if (-not (Test-Path $Pythonw)) {
    $Pythonw = $Python
}

$Arguments = "`"$MonitorScript`" --config `"$ConfigFile`""
$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument $Arguments `
    -WorkingDirectory $ScriptDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "WatchFrog überwacht Audiostreams auf empfangene Stille." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Fertig: WatchFrog läuft als geplante Aufgabe."
Write-Host "Logdatei: $ConfigRoot\logs\watchfrog.log"
