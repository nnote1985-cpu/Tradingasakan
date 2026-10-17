# Restarts the Testnet worker so it runs the code now checked out (use after git pull).
#   powershell -ExecutionPolicy Bypass -File scripts\windows\restart-worker.ps1
& "$PSScriptRoot\stop-worker.ps1"
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName "Tradingasakan Testnet worker"
"started: Tradingasakan Testnet worker"
