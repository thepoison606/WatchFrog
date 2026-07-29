$ErrorActionPreference = "Stop"

$TaskName = "WatchFrog"
$ConfigRoot = Join-Path $env:APPDATA "WatchFrog"
$LogFile = Join-Path $ConfigRoot "logs\watchfrog.log"

Write-Host ""
Write-Host "WatchFrog – Windows-Status"
Write-Host "=========================="
Write-Host ""

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task -and $Task.State -eq "Running") {
    Write-Host "Status: läuft im Hintergrund"
} elseif ($Task) {
    Write-Host "Status: Aufgabe vorhanden, aber nicht aktiv ($($Task.State))"
    Write-Host "Starten mit: Start-ScheduledTask -TaskName `"$TaskName`""
} else {
    Write-Host "Status: nicht eingerichtet"
    Write-Host "Bitte setup-windows.ps1 ausführen."
}

Write-Host ""
if (Test-Path $LogFile) {
    Write-Host "Letzte Meldungen:"
    Write-Host ""
    Get-Content -Path $LogFile -Tail 25
} else {
    Write-Host "Es gibt noch keine Logdatei unter:"
    Write-Host $LogFile
}
