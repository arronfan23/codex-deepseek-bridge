#!/usr/bin/env bash
set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Codex DeepSeek Bridge                ║"
echo "║   Responses API <--> Chat API          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 .env
if [ ! -f ".env" ]; then
    echo "[WARN] 未找到 .env 文件"
    echo "      请复制 .env.example 为 .env 并填入你的 DeepSeek API Key"
    echo ""
fi

# 杀掉占用 11435 的旧进程
OLD_PID=$(lsof -ti:11435 2>/dev/null || netstat -ano 2>/dev/null | grep 11435 | grep LISTENING | awk '{print $NF}')
if [ -n "$OLD_PID" ]; then
    echo "[INFO] 杀掉旧进程 PID:$OLD_PID"
    kill -9 "$OLD_PID" 2>/dev/null || taskkill //F //PID "$OLD_PID" 2>/dev/null
    sleep 1
fi

# 检查 Python
if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
    echo "[ERROR] 未找到 Python，请先安装 Python 3.10+"
    exit 1
fi

PYTHON=$(command -v python3 || command -v python)
echo "[INFO] 启动桥接代理..."
exec "$PYTHON" -B -u deepseek-bridge.py
