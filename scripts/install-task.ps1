param(
  [switch]$Uninstall,
  [string]$Python = "$PSScriptRoot\..\.venv\Scripts\python.exe",
  [string]$Project = (Resolve-Path "$PSScriptRoot\..").Path,
  [string]$MorningAt = "22:05",
  [string]$AfternoonAt = "12:30",
  [string]$EveningAt = "19:10",
  [int]$RepeatMinutes = 10
)

$ErrorActionPreference = "Stop"
$taskNames = @(
  "SeatAssistant-Morning",
  "SeatAssistant-Afternoon",
  "SeatAssistant-Evening",
  "SeatAssistant"
)

function Remove-SeatAssistantTasks {
  foreach ($name in $taskNames) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
      Unregister-ScheduledTask -TaskName $name -Confirm:$false
      Write-Host "已删除计划任务：$name"
    }
  }
}

if ($Uninstall) {
  Remove-SeatAssistantTasks
  Write-Host "SeatAssistant 定时任务已卸载。"
  exit 0
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
  throw "找不到 Python：$Python"
}
$projectPath = (Resolve-Path -LiteralPath $Project).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
if ($RepeatMinutes -lt 5 -or $RepeatMinutes -gt 60) {
  throw "RepeatMinutes 必须在 5 到 60 分钟之间。"
}

Remove-SeatAssistantTasks
$settings = New-ScheduledTaskSettingsSet `
  -Hidden `
  -WakeToRun `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited

$definitions = @(
  @{ Name = "SeatAssistant-Morning"; Period = "morning"; At = $MorningAt; Duration = 30 },
  @{ Name = "SeatAssistant-Afternoon"; Period = "afternoon"; At = $AfternoonAt; Duration = 30 },
  @{ Name = "SeatAssistant-Evening"; Period = "evening"; At = $EveningAt; Duration = 20 }
)

foreach ($item in $definitions) {
  $at = [datetime]::ParseExact($item.At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
  $triggers = @()
  for ($offset = 0; $offset -le $item.Duration; $offset += $RepeatMinutes) {
    $triggerAt = $at.AddMinutes($offset)
    $triggers += New-ScheduledTaskTrigger -Daily -At $triggerAt
  }
  $action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "-m scripts.run_scheduled_task --period $($item.Period)" `
    -WorkingDirectory $projectPath
  Register-ScheduledTask `
    -TaskName $item.Name `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Seat Assistant 无感预约：$($item.Period)" `
    -Force | Out-Null
  Write-Host "已安装：$($item.Name)，每天 $($item.At) 起每 $RepeatMinutes 分钟检查一次。"
}

Write-Host "SeatAssistant 无感定时任务安装完成。电脑可锁屏或睡眠，任务会尝试唤醒电脑；浏览器使用后台模式。"
