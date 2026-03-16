#!/bin/bash
set -e

echo "=== Image & Video Relay Service 部署脚本 ==="

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "安装 Python3..."
    apt-get update -y && apt-get install -y python3 python3-pip python3-venv
fi

# 创建虚拟环境
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q fastapi "uvicorn[standard]" httpx qiniu python-dotenv pydantic

# 检查 .env
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  请先编辑 .env 文件填入配置，然后重新运行此脚本"
    echo "    nano .env"
    exit 1
fi

# 后台启动
pkill -f "uvicorn main:app" 2>/dev/null || true
nohup uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2 > /var/log/image-relay.log 2>&1 &

sleep 2
if curl -s http://localhost:8000/health | grep -q "ok"; then
    echo ""
    echo "✅ 服务启动成功！"
    echo "   地址：http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP'):8000"
    echo "   日志：tail -f /var/log/image-relay.log"
else
    echo "❌ 启动失败，查看日志："
    tail -20 /var/log/image-relay.log
fi
