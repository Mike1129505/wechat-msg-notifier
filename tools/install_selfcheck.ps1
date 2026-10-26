# 注册「微信消息提醒-自检」计划任务：每 10 分钟跑一次 tools\selfcheck.py
# 用法： powershell -ExecutionPolicy Bypass -File tools\install_selfcheck.ps1 [-Minutes 10]
param([int]$Minutes = 10)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $py)) { throw "找不到 $py，请先运行 install.ps1" }

$taskName = "微信消息提醒-自检"
$arg = '"' + (Join-Path $Root "tools\selfcheck.py") + '" --notify'
$action = New-ScheduledTaskAction -Execute $py -Argument $arg -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "每 $Minutes 分钟检查微信提醒进程、心跳、窗口和日志；异常时推送手机。不自动重启。" -Force | Out-Null

Write-Host "已注册计划任务：$taskName（每 $Minutes 分钟）"
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State | Format-Table -AutoSize