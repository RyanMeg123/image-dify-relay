# Image & Video Relay Service

Dify → 本服务 → AIHubMix → 七牛云 → 返回 URL

## 架构

```
用户输入 prompt
    ↓
Dify 工作流（条件分支 image/video）
    ↓
本中转服务（FastAPI）
    ↓
AIHubMix（Gemini 图片 / Seedance 视频）
    ↓
七牛云存储
    ↓
返回永久 URL → Dify → 用户
```

---

## 服务器部署

### 1. 准备服务器

推荐：Ubuntu 20.04+ / Debian 11+，1核2G 以上

确保以下端口已在安全组/防火墙开放：
- **22**：SSH
- **8000**：本服务

### 2. 安装依赖

```bash
apt-get update -y && apt-get install -y python3-pip python3-venv git
```

### 3. 拉取代码

```bash
cd /root
git clone https://github.com/RyanMeg123/image-dify-relay.git
cd image-dify-relay
```

### 4. 配置环境变量

```bash
cp .env.example .env
nano .env
```

按照下方「环境变量说明」填入真实配置，保存退出（Ctrl+X → Y → Enter）。

### 5. 一键启动

```bash
chmod +x deploy.sh && ./deploy.sh
```

启动成功后会显示：
```
✅ 服务启动成功！
   地址：http://YOUR_IP:8000
```

### 6. 开放防火墙

```bash
ufw allow 8000/tcp
```

### 7. 验证服务

```bash
curl http://localhost:8000/health
# 返回 {"status":"ok","tasks":0} 即正常
```

### 重启服务

```bash
fuser -k 8000/tcp; sleep 2
nohup /root/image-dify-relay/venv/bin/uvicorn main:app \
  --host 0.0.0.0 --port 8000 --workers 2 \
  > /var/log/image-relay.log 2>&1 &
```

### 查看日志

```bash
tail -f /var/log/image-relay.log
```

### 更新代码

```bash
cd /root/image-dify-relay
git pull origin main
fuser -k 8000/tcp; sleep 2
nohup /root/image-dify-relay/venv/bin/uvicorn main:app \
  --host 0.0.0.0 --port 8000 --workers 2 \
  > /var/log/image-relay.log 2>&1 &
```

---

## 环境变量说明（.env）

| 变量 | 说明 | 示例 |
|------|------|------|
| `AIHUBMIX_API_KEY` | AIHubMix API Key | `sk-xxx` |
| `IMAGE_MODEL` | 图片生成模型 | `gemini-3.1-flash-image-preview` |
| `VIDEO_MODEL` | 视频生成模型 | `doubao-seedance-1-5-pro-251215` |
| `QINIU_ACCESS_KEY` | 七牛云 AccessKey | `cuKURCxxx` |
| `QINIU_SECRET_KEY` | 七牛云 SecretKey | `pWCg3xxx` |
| `QINIU_BUCKET` | 七牛云 Bucket 名 | `my-bucket` |
| `QINIU_DOMAIN` | 七牛云访问域名（不含 http://） | `xxx.clouddn.com` |
| `API_TOKEN` | 可选鉴权 Token，留空则不鉴权 | `secret123` |

> ⚠️ 七牛云 Bucket 需设置为**公开空间**，否则生成的 URL 无法直接访问。

---

## API 接口

### 图片生成（同步，约 30-60 秒）

```
POST /generate-image
Content-Type: application/json

{
  "prompt": "a cute cat",
  "size": "1024x1024"
}
```

返回：
```json
{
  "url": "http://xxx.clouddn.com/ai-images/xxx.png",
  "markdown": "![generated](http://...)"
}
```

### 视频生成—同步（适合 Dify 单节点，约 2-5 分钟）

```
POST /generate-video-sync
Content-Type: application/json

{
  "prompt": "an astronaut floating in space",
  "size": "1080p",
  "seconds": "5"
}
```

返回：
```json
{
  "url": "http://xxx.clouddn.com/ai-videos/xxx.mp4",
  "markdown": "[▶ 点击播放视频](http://...)"
}
```

### 视频生成—异步（分两步）

**提交任务：**
```
POST /generate-video
→ 返回 {"task_id": "task_abc123", "status": "processing"}
```

**轮询状态：**
```
GET /video-status/{task_id}
→ status: pending / processing / done / failed
→ done 时返回 url 和 markdown
```

### 健康检查

```
GET /health
→ {"status": "ok", "tasks": 0}
```

---

## Dify 配置

### 服务地址

| 部署方式 | HTTP 请求地址 |
|---------|-------------|
| **云端服务器**（推荐） | `http://YOUR_SERVER_IP:8000` |
| **本地部署 Dify**（同机器） | `http://host.docker.internal:8000`（Mac/Windows）或 `http://172.17.0.1:8000`（Linux） |
| **本地部署 Dify**（不同机器） | `http://中转服务所在机器IP:8000` |

> 如果 Dify 是 Docker 部署，容器内无法访问宿主机的 `localhost`，需要用 `host.docker.internal`（Mac）或网关 IP（Linux）。

### 工作流设计（图片+视频二合一）

**开始节点变量：**
- `prompt`（文本）
- `type`（文本，值填 `image` 或 `video`）

**节点连接：**
```
开始
  ↓
条件分支（type 包含 image → CASE1，type 包含 video → CASE2）
  ├── CASE1 → HTTP POST /generate-image（超时 180s）→ Code解析 → 结束
  └── CASE2 → HTTP POST /generate-video-sync（超时 600s）→ Code解析 → 结束
```

**HTTP 节点 Body：**
```json
{"prompt": "{{#开始.prompt#}}"}
```

**Code 节点（Python）：**
```python
import json

def main(body: str) -> dict:
    data = json.loads(body)
    return {"url": data["url"]}
```

**如设置了 API_TOKEN，HTTP 节点 Headers 加：**
```
X-Api-Token: your_token
```
