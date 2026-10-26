param(
    [switch]$Autostart,      # 顺便设置开机自启 + 关闭自动睡眠
    [switch]$SkipPip         # 已经装好依赖时跳过
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$Root = $PSScriptRoot

Write-Host "=== 微信新消息提醒 安装程序 ===" -ForegroundColor Cyan

function Find-Python {
    foreach ($cand in @("py", "python")) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) {
            try {
                $ver = & $cand -c "import sys;print(sys.version_info.major, sys.version_info.minor)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $ver) {
                    $parts = $ver -split ' '
                    if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) { return $cand }
                }
            } catch { }
        }
    }
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
                     "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")) {
        if (Test-Path $p) { return $p }
    }
    throw "找不到 Python 3.10+。请先安装：https://www.python.org/downloads/ （安装时勾选 Add to PATH）"
}

$py = Find-Python
Write-Host ("Python: " + $py)

if (-not (Test-Path (Join-Path $Root ".venv\Scripts\python.exe"))) {
    Write-Host "创建虚拟环境 .venv ..."
    & $py -m venv .venv
}

$vpy = Join-Path $Root ".venv\Scripts\python.exe"

if (-not $SkipPip) {
    Write-Host "升级 pip ..."
    & $vpy -m pip install --upgrade pip --quiet
    Write-Host "安装依赖（pywin32 / pillow / rapidocr-onnxruntime，约 100MB）..."
    & $vpy -m pip install pywin32 pillow rapidocr-onnxruntime --quiet
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
}

Write-Host "验证 OCR 引擎 ..."
& $vpy -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('OCR OK')"
if ($LASTEXITCODE -ne 0) { throw "OCR 引擎加载失败" }

# 桌面快捷方式
$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop '微信消息提醒.lnk'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnkPath)
$sc.TargetPath = Join-Path $Root 'notify.bat'
$sc.WorkingDirectory = $Root
$sc.Description = '微信新消息提醒（只读，不操作微信）'
$sc.IconLocation = 'C:\Windows\System32\shell32.dll,168'
$sc.Save()
Write-Host ("桌面快捷方式: " + $lnkPath)

if ($Autostart) {
    Write-Host "设置开机自启 ..."
    $startup = [Environment]::GetFolderPath('Startup')
    $auto = Join-Path $startup '微信消息提醒.lnk'
    $sc2 = $ws.CreateShortcut($auto)
    $sc2.TargetPath = Join-Path $Root 'notify.bat'
    $sc2.WorkingDirectory = $Root
    $sc2.Description = '微信新消息提醒（开机自启）'
    $sc2.IconLocation = 'C:\Windows\System32\shell32.dll,168'
    $sc2.Save()
    Write-Host ("  自启项: " + $auto)

    Write-Host "关闭自动睡眠/休眠（插电时）..."
    & powercfg /change standby-timeout-ac 0  2>$null
    & powercfg /change hibernate-timeout-ac 0 2>$null
    & powercfg /change monitor-timeout-ac 10  2>$null
    Write-Host "  提示：锁屏仍需手动关闭（设置→账户→登录选项→需要登录：从不）"
}

Write-Host ""
Write-Host "安装完成。下一步：" -ForegroundColor Green
Write-Host "  1) 登录电脑版微信（小号），窗口保持默认大小、不要最小化"
Write-Host "  2) 自检： .\.venv\Scripts\python.exe tools\doctor.py"
Write-Host "  3) 试推： .\.venv\Scripts\python.exe watch_notify.py --test"
Write-Host "  4) 启动： 双击桌面「微信消息提醒」或运行 .\.venv\Scripts\python.exe watch_notify.py"
