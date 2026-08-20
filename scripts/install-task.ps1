param(
  [string]$Python = "$PSScriptRoot\..\.venv\Scripts\python.exe",
  [string]$Project = (Resolve-Path "$PSScriptRoot\..").Path
)
$action = New-ScheduledTaskAction -Execute $Python -Argument "-m seat_assistant.main" -WorkingDirectory $Project
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "SeatAssistant" -Action $action -Trigger $trigger -Description "Local seat reservation assistant" -Force
Write-Host "SeatAssistant task installed."
