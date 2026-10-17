# Keeps the Testnet worker running on the PC (testnet-development-plan.md §2.2).
# Restarts it after a crash, waiting longer after crashes that come close together.
# Exit 0 (Ctrl+C) ends the loop. Output goes to var\worker\worker.log.
param([string]$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path)
Set-Location $Repo
$log = Join-Path $Repo "var\worker\worker.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$delay = 5
while ($true) {
    $started = Get-Date
    uv run python scripts/testnet_worker.py *>> $log
    $code = $LASTEXITCODE
    if ($code -eq 0) { break }
    if (((Get-Date) - $started).TotalMinutes -gt 10) { $delay = 5 } else { $delay = [Math]::Min($delay * 2, 300) }
    if ($code -eq 2) { $delay = 300 }   # configuration refused: retrying fast will not help
    "$(Get-Date -Format o) worker exited with $code; restarting in $delay s" | Add-Content $log
    Start-Sleep -Seconds $delay
}
