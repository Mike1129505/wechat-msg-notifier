$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Show-Process($pattern, $label) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like $pattern }
    if ($procs) {
        foreach ($p in $procs) {
            $alive = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
            $since = if ($alive) { $alive.StartTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '?' }
            Write-Host ("  " + $label + " 正在运行  pid=" + $p.ProcessId + "  启动于 " + $since) -ForegroundColor Green
        }
    } else {
        Write-Host ("  " + $label + " 没在运行") -ForegroundColor DarkGray
    }
}

Write-Host "==== 进程 ====" -ForegroundColor Cyan
Show-Process "*run.py*" "转发"
Show-Process "*watch_notify.py*" "提醒"

Write-Host ""
Write-Host "==== 微信窗口 ====" -ForegroundColor Cyan
$venv = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venv) {
    & $venv (Join-Path $root "tools\about_state.py") 2>&1
}

Write-Host ""
Write-Host "==== 转发日志 ====" -ForegroundColor Cyan
$log = Join-Path $root "forwarder.log"
if (Test-Path $log) { Get-Content $log -Tail 6 -Encoding UTF8 | ForEach-Object { "  $_" } }
else { Write-Host "  暂无" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "==== 提醒日志 ====" -ForegroundColor Cyan
$nlog = Join-Path $root "notify.log"
if (Test-Path $nlog) { Get-Content $nlog -Tail 6 -Encoding UTF8 | ForEach-Object { "  $_" } }
else { Write-Host "  暂无" -ForegroundColor DarkGray }
Write-Host ""
