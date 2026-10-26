$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot

$found = $false
foreach ($pattern in @("*run.py*", "*watch_notify.py*")) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like $pattern } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host ("已停止 pid=" + $_.ProcessId) -ForegroundColor Green
            $script:found = $true
        }
}

Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
    Where-Object { $_.CommandLine -like '*微信小号*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

if (-not $found) { Write-Host "没有在运行的转发/提醒进程。" -ForegroundColor Yellow }
else { Write-Host "已停止。" -ForegroundColor Green }
