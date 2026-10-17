# Stops the Testnet worker completely: the scheduled task, and any worker process it left behind.
# Stop-ScheduledTask ends only the PowerShell loop; `uv run` and Python keep running (and keep the
# writer lease and the log file) unless they are stopped here too.
#   powershell -ExecutionPolicy Bypass -File scripts\windows\stop-worker.ps1
param([switch]$ProcessesOnly)
$name = "Tradingasakan Testnet worker"
if (-not $ProcessesOnly) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
}
$left = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*testnet_worker.py*" }
foreach ($p in $left) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    "stopped $($p.Name) (pid $($p.ProcessId))"
}
if (-not $left) { "no worker process was running" }
