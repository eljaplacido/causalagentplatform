$pids = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess | Sort-Object -Unique
foreach ($p in $pids) {
    $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "PID=$p Name=$($proc.ProcessName) Path=$($proc.Path)"
    } else {
        Write-Host "PID=$p (not found by Get-Process)"
    }
}
