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
  "SeatAssistant-Dynamic-Morning",
  "SeatAssistant-Dynamic-Afternoon",
  "SeatAssistant-Dynamic-Evening",
  "SeatAssistant-Dynamic-Period04",
  "SeatAssistant-Dynamic-Period05",
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

Write-Host "SeatAssistant 无感定时任务安装完成。电脑可锁屏或睡眠，任务会尝试唤醒电脑；动态任务结束时会关闭其托管的企业微信机器人。"
