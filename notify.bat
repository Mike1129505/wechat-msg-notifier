@echo off
cd /d "%~dp0"
title 微信消息提醒
if not exist ".venv\Scripts\python.exe" (
    echo 还没有安装，请先运行 install.ps1
    pause
    exit /b 1
)
.venv\Scripts\python.exe watch_notify.py
echo.
echo 程序已退出。
pause