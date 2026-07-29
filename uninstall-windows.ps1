$ErrorActionPreference = "Stop"

$TaskName = "WatchFrog"
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Write-Host "Der WatchFrog-Autostart wurde entfernt. Konfiguration und Logs bleiben erhalten."
