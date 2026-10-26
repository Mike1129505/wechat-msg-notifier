@echo off
cd /d "%~dp0"
title 微信提醒自检
if not exist ".venv\Scripts\python.exe" (
    echo 还没有安装，请先运行 install.ps1
    pause
    exit /b 1
)
.venv\Scripts\python.exe tools\selfcheck.py %*
echo.
pause