# Daily trading cycle for Windows. Register once (PowerShell, from the project folder):
#   $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$PWD\deploy\run_daily.ps1`""
#   (add  -Live  after the .ps1 path to also run the LIVE account)
#   $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 8:35AM
#   $s = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable
#   Register-ScheduledTask -TaskName "SentinelTrader" -Action $a -Trigger $t -Settings $s
# (8:35 AM Central = 9:35 AM Eastern.)
param([string]$Settings = "config/settings_etf.yaml", [switch]$Live)
Set-Location (Split-Path $PSScriptRoot -Parent)
& .\.venv\Scripts\Activate.ps1
New-Item -ItemType Directory -Force -Path state | Out-Null
"==== $(Get-Date -Format s) ====" | Out-File -Append -Encoding utf8 state\run.log
python -m sentinel --settings $Settings run-once --broker alpaca_paper *>> state\run.log
if ($Live) { python -m sentinel --settings $Settings run-once --broker alpaca_live *>> state\run.log }
python -m sentinel --settings $Settings report *>> state\run.log
