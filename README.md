# Image & Video Relay Service

Dify → 本服务 → AIHubMix → 七牛云 → 返回 URL

## 快速部署

```bash
git clone https://github.com/RyanMeg123/image-dify-relay.git
cd image-dify-relay

# 填写配置
cp .env.example .env
nano .env   # 填入 API Key 等配置

# 一键启动
chmod +x deploy.sh
./deploy.sh
```

## 环境变量说明（.env）

| 变量 | 说明 | 示例 |
|------|------|------|
| `AIHUBMIX_API_KEY` | AIHubMix API Key | `sk-xxx` |
| `IMAGE_MODEL` | 图片生成模型 | `gemini-3.1-flash-image-preview` |
| `VIDEO_MODEL` | 视频生成模型 | `doubao-seedance-1-5-pro-251215` |
| `QINIU_ACCESS_KEY` | 七牛 AccessKey | - |
| `QINIU_SECRET_KEY` | 七牛 SecretKey | - |
| `QINIU_BUCKET` | 七牛 Bucket 名 | `my-bucket` |
| `QINIU_DOMAIN` | 七牛访问域名 | `xxx.clouddn.com` |
| `API_TOKEN` | 可选鉴权 Token（留空不鉴权）| `secret123` |

## API 接口

### 图片生成（同步）

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

### 视频生成（异步）

**第一步：提交任务**
```
POST /generate-video
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
  "task_id": "task_abc12345",
  "status": "processing",
  "message": "视频生成中..."
}
```

**第二步：轮询状态**
```
GET /video-status/{task_id}
```

返回（完成时）：
```json
{
  "task_id": "task_abc12345",
  "status": "done",
  "url": "http://xxx.clouddn.com/ai-videos/xxx.mp4",
  "markdown": "[▶ 点击播放视频](http://...)"
}
```

### 健康检查
```
GET /health
```

## Dify 配置

在 Dify 工作流中添加 HTTP 节点：
- **图片**：`POST http://YOUR_IP:8000/generate-image`
- **视频提交**：`POST http://YOUR_IP:8000/generate-video`
- **视频查询**：`GET http://YOUR_IP:8000/video-status/{{task_id}}`

如设置了 `API_TOKEN`，请求头加：`X-Api-Token: your_token`
