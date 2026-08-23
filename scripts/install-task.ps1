param(
  [switch]$Uninstall,
  [switch]$DryRun,
  [string]$Python = "$PSScriptRoot\..\.venv\Scripts\python.exe",
  [string]$Project = (Resolve-Path "$PSScriptRoot\..").Path,
  [string]$MorningAt = "22:05",
  [string]$AfternoonAt = "12:30",
  [string]$EveningAt = "19:10",
  [string]$Period04At = "10:05",
  [string]$Period05At = "13:05",
  [int]$RepeatMinutes = 10
)

$ErrorActionPreference = "Stop"
$taskNames = @(
  "SeatAssistant-Morning",
  "SeatAssistant-Afternoon",
  "SeatAssistant-Evening",
  "SeatAssistant-Period04",
  "SeatAssistant-Period05",
  "SeatAssistant-Bot-Morning",
  "SeatAssistant-Bot-Morning-Fallback",
  "SeatAssistant-Bot-Afternoon",
  "SeatAssistant-Bot-Evening",
  "SeatAssistant-Bot-Period04",
  "SeatAssistant-Bot-Period05"
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
  -RunOnlyIfNetworkAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited

$definitions = @(
  @{ Name = "SeatAssistant-Morning"; Period = "morning"; At = $MorningAt; Duration = 30; FallbackAt = "07:00" },
  @{ Name = "SeatAssistant-Afternoon"; Period = "afternoon"; At = $AfternoonAt; Duration = 30 },
  @{ Name = "SeatAssistant-Evening"; Period = "evening"; At = $EveningAt; Duration = 20 },
  @{ Name = "SeatAssistant-Period04"; Period = "period04"; At = $Period04At; Duration = 20 },
  @{ Name = "SeatAssistant-Period05"; Period = "period05"; At = $Period05At; Duration = 20 }
)

foreach ($item in $definitions) {
  $at = [datetime]::ParseExact($item.At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
  $triggers = @()
  for ($offset = 0; $offset -le $item.Duration; $offset += $RepeatMinutes) {
    $triggerAt = $at.AddMinutes($offset)
    $triggers += New-ScheduledTaskTrigger -Daily -At $triggerAt
  }
  if ($item.FallbackAt) {
    $fallbackAt = [datetime]::ParseExact($item.FallbackAt, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
    $triggers += New-ScheduledTaskTrigger -Daily -At $fallbackAt
  }
  $action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument ("-m scripts.run_scheduled_task --period $($item.Period)" + $(if ($DryRun) { " --dry-run" } else { "" })) `
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

$botDefinitions = @(
  @{ Name = "SeatAssistant-Bot-Morning"; At = $MorningAt; Duration = 46 },
  @{ Name = "SeatAssistant-Bot-Morning-Fallback"; At = "07:00"; Duration = 16 },
  @{ Name = "SeatAssistant-Bot-Afternoon"; At = $AfternoonAt; Duration = 46 },
  @{ Name = "SeatAssistant-Bot-Evening"; At = $EveningAt; Duration = 36 },
  @{ Name = "SeatAssistant-Bot-Period04"; At = $Period04At; Duration = 36 },
  @{ Name = "SeatAssistant-Bot-Period05"; At = $Period05At; Duration = 36 }
)

$botSettings = New-ScheduledTaskSettingsSet `
  -Hidden `
  -WakeToRun `
  -StartWhenAvailable `
  -RunOnlyIfNetworkAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -RestartCount 5 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 60)

foreach ($item in $botDefinitions) {
  $at = [datetime]::ParseExact($item.At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
  $startAt = $at.AddMinutes(-1)
  $trigger = New-ScheduledTaskTrigger -Daily -At $startAt
  $action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument ("-m scripts.run_wecom_bot --run-for-minutes $($item.Duration)") `
    -WorkingDirectory $projectPath
  Register-ScheduledTask `
    -TaskName $item.Name `
    -Action $action `
    -Trigger $trigger `
    -Settings $botSettings `
    -Principal $principal `
    -Description "Seat Assistant 企业微信机器人：$($item.Duration) 分钟" `
    -Force | Out-Null
  Write-Host "已安装：$($item.Name)，每天 $($startAt.ToString('HH:mm')) 启动，运行 $($item.Duration) 分钟。"
}

Write-Host "SeatAssistant 无感定时任务安装完成。电脑可锁屏或睡眠，任务会尝试唤醒电脑；浏览器使用后台模式。"
