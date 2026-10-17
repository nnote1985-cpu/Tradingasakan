# Registers the Testnet worker as a Windows scheduled task that starts at logon and runs hidden.
# The task sees user environment variables, so set the keys with `setx`, not `set`.
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-worker-task.ps1
#   Start-ScheduledTask "Tradingasakan Testnet worker"        # start now
#   Stop-ScheduledTask "Tradingasakan Testnet worker"         # stop (the dead-man will alert after 5 min)
#   Unregister-ScheduledTask "Tradingasakan Testnet worker"   # remove
$name = "Tradingasakan Testnet worker"
$script = Join-Path $PSScriptRoot "testnet-worker.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
"registered: $name (starts at logon; Start-ScheduledTask `"$name`" to start now)"
