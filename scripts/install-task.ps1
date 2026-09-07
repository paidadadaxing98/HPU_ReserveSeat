param(
  [switch]$Uninstall,
  [switch]$DryRun,
  [string]$Python = "$PSScriptRoot\..\.venv\Scripts\python.exe",
  [string]$Project = (Resolve-Path "$PSScriptRoot\..").Path,
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
  "SeatAssistant-Dynamic-Morning",
  "SeatAssistant-Dynamic-Afternoon",
  "SeatAssistant-Dynamic-Evening",
  "SeatAssistant-Dynamic-Period04",
  "SeatAssistant-Dynamic-Period05",
  "SeatAssistant-Bot-Daily",
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
  @{ Name = "SeatAssistant-Morning"; Period = "morning" },
  @{ Name = "SeatAssistant-Afternoon"; Period = "afternoon" },
  @{ Name = "SeatAssistant-Evening"; Period = "evening" },
  @{ Name = "SeatAssistant-Period04"; Period = "period04"; FallbackAt = $Period04At; FallbackDuration = 20 },
  @{ Name = "SeatAssistant-Period05"; Period = "period05"; FallbackAt = $Period05At; FallbackDuration = 20 }
)

foreach ($item in $definitions) {
  $scheduleArgs = @(
    "-m", "scripts.reservation_task_schedule",
    "--period", $item.Period,
    "--repeat-minutes", $RepeatMinutes
  )
  if ($item.FallbackAt) {
    $scheduleArgs += @(
      "--fallback-start", $item.FallbackAt,
      "--fallback-duration-minutes", $item.FallbackDuration
    )
  }
  $scheduleOutput = & $pythonPath @scheduleArgs
  if ($LASTEXITCODE -ne 0) {
    throw "无法计算预约任务时间：$($item.Period)"
  }
  try {
    $schedule = ($scheduleOutput -join "`n") | ConvertFrom-Json
  }
  catch {
    throw "预约任务时间输出不是有效 JSON：$($item.Period)"
  }
  if (-not $schedule.enabled) {
    Write-Host "已跳过：$($item.Name)（$($schedule.message)）"
    continue
  }
  $triggerTimes = @($schedule.triggers)
  if ($triggerTimes.Count -eq 0) {
    throw "预约任务没有可用触发时间：$($item.Period)"
  }
  $triggers = @()
  foreach ($triggerText in $triggerTimes) {
    $triggerAt = [datetime]::ParseExact(
      [string]$triggerText,
      "HH:mm",
      [Globalization.CultureInfo]::InvariantCulture
    )
    $triggers += New-ScheduledTaskTrigger -Daily -At $triggerAt
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
  Write-Host "已安装：$($item.Name)，每天触发 $($triggerTimes -join ', ')。"
}

$dynamicPeriods = @(
  @{ Name = "SeatAssistant-Dynamic-Morning"; Period = "morning" },
  @{ Name = "SeatAssistant-Dynamic-Afternoon"; Period = "afternoon" },
  @{ Name = "SeatAssistant-Dynamic-Evening"; Period = "evening" },
  @{ Name = "SeatAssistant-Dynamic-Period04"; Period = "period04" },
  @{ Name = "SeatAssistant-Dynamic-Period05"; Period = "period05" }
)

foreach ($item in $dynamicPeriods) {
  $scheduleOutput = & $pythonPath -m scripts.dynamic_task_schedule --period $item.Period
  if ($LASTEXITCODE -ne 0) {
    throw "无法计算动态任务时间：$($item.Period)"
  }
  try {
    $schedule = ($scheduleOutput -join "`n") | ConvertFrom-Json
  }
  catch {
    throw "动态任务时间输出不是有效 JSON：$($item.Period)"
  }
  if (-not $schedule.enabled) {
    Write-Host "已跳过：$($item.Name)（$($schedule.message)）"
    continue
  }
  $startAt = [datetime]::ParseExact(
    $schedule.start,
    "HH:mm",
    [Globalization.CultureInfo]::InvariantCulture
  )
  $duration = [int]$schedule.duration_minutes
  if ($duration -le 0) {
    throw "动态任务运行时长必须大于 0：$($item.Period)"
  }
  $dynamicSettings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -WakeToRun `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes ($duration + 15))
  $dynamicArguments = "-m scripts.run_dynamic_monitor --period $($item.Period) --run-for-minutes $duration"
  if ($DryRun) {
    $dynamicArguments += " --dry-run"
  }
  $action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument $dynamicArguments `
    -WorkingDirectory $projectPath
  $trigger = New-ScheduledTaskTrigger -Daily -At $startAt
  Register-ScheduledTask `
    -TaskName $item.Name `
    -Action $action `
    -Trigger $trigger `
    -Settings $dynamicSettings `
    -Principal $principal `
    -Description "Seat Assistant 动态补偿：$($item.Period)，$($schedule.start)-$($schedule.end)" `
    -Force | Out-Null
  Write-Host "已安装：$($item.Name)，每天 $($schedule.start) 启动，运行 $duration 分钟至 $($schedule.end)。"
}

$botDailyAt = "07:00"
$botDailyDuration = 921
$botDailyEnd = ([datetime]::ParseExact(
  $botDailyAt,
  "HH:mm",
  [Globalization.CultureInfo]::InvariantCulture
) + (New-TimeSpan -Minutes $botDailyDuration)).ToString("HH:mm")
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
  -ExecutionTimeLimit (New-TimeSpan -Minutes ($botDailyDuration + 15))
$botDailyAction = New-ScheduledTaskAction `
  -Execute $pythonPath `
  -Argument "-m scripts.run_wecom_bot --run-for-minutes $botDailyDuration" `
  -WorkingDirectory $projectPath
$botDailyTrigger = New-ScheduledTaskTrigger `
  -Daily `
  -At ([datetime]::ParseExact($botDailyAt, "HH:mm", [Globalization.CultureInfo]::InvariantCulture))
# The bot must survive an unexpected exit (for example a mid-day sleep or a
# killed websocket): re-arm it every 30 minutes for the rest of the day.
# MultipleInstances=IgnoreNew keeps a healthy instance from being duplicated.
$botRepetition = New-ScheduledTaskTrigger `
  -Once `
  -At ([datetime]::ParseExact($botDailyAt, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)) `
  -RepetitionInterval (New-TimeSpan -Minutes 30) `
  -RepetitionDuration (New-TimeSpan -Hours 23)
$botDailyTrigger.Repetition = $botRepetition.Repetition
Register-ScheduledTask `
  -TaskName "SeatAssistant-Bot-Daily" `
  -Action $botDailyAction `
  -Trigger $botDailyTrigger `
  -Settings $botSettings `
  -Principal $principal `
  -Description "Seat Assistant 企业微信机器人：$botDailyAt-$botDailyEnd" `
  -Force | Out-Null
Write-Host "已安装：SeatAssistant-Bot-Daily，每天 $botDailyAt 启动，运行 $botDailyDuration 分钟至 $botDailyEnd。"

Write-Host "SeatAssistant 无感定时任务安装完成。电脑可锁屏或睡眠，任务会尝试唤醒电脑；全天机器人任务在 $botDailyAt-$botDailyEnd 在线。"
