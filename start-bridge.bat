@echo off
chcp 650010 >nul
title Codex DeepSeek Bridge

echo.
echo ╔══════════════════════════════════════════╗
echo ║   Codex DeepSeek Bridge                ║
echo ║   Responses API ^<--^> Chat API          ║
echo ╚══════════════════════════════════════════╝
echo.

REM 检查 .env 文件
if not exist ".env" (
    echo [WARN] 未找到 .env 文件
    echo       请复制 .env.example 为 .env 并填入你的 DeepSeek API Key
    echo.
)

REM 杀掉占用 18035 端口的旧进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":18035" ^| findstr "LISTENING"') do (
    echo [INFO] 杀掉旧进程 PID:%%a
    taskkill /F /PID %%a >nul 2>&1
)

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

echo [INFO] 启动桥接代理...
python -B -u deepseek-bridge.py

pause
