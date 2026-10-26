@echo off
cd /d "%~dp0"
title 微信转发（旧模式）
if not exist ".venv\Scripts\python.exe" (
    echo 还没有安装，请先运行 install.ps1
    pause
    exit /b 1
)
.venv\Scripts\python.exe run.py
echo.
echo 程序已退出。
pause