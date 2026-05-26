$pids = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess | Sort-Object -Unique
foreach ($p in $pids) {
    Write-Host "Stopping PID $p"
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
}
Write-Host "Done"
