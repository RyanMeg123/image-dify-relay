"""
Image & Video Relay Service
用户 -> Dify -> 本服务 -> AIHubMix -> 七牛云 -> 返回 URL -> Dify -> 用户
"""

import os
import base64
import uuid
import asyncio
import httpx
from qiniu import Auth, put_data
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Image & Video Relay Service", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 配置 ──────────────────────────────────────────────
AIHUBMIX_API_KEY  = os.getenv("AIHUBMIX_API_KEY")
AIHUBMIX_BASE_URL = "https://aihubmix.com/v1"
IMAGE_MODEL       = os.getenv("IMAGE_MODEL", "gemini-2.0-flash-preview-image-generation")
VIDEO_MODEL       = os.getenv("VIDEO_MODEL", "doubao-seedance-1-5-pro-251215")

QINIU_ACCESS_KEY  = os.getenv("QINIU_ACCESS_KEY")
QINIU_SECRET_KEY  = os.getenv("QINIU_SECRET_KEY")
QINIU_BUCKET      = os.getenv("QINIU_BUCKET")
QINIU_DOMAIN      = os.getenv("QINIU_DOMAIN")   # e.g. tbzcavv2k.hd-bkt.clouddn.com

API_TOKEN = os.getenv("API_TOKEN", "")

# 任务状态内存存储
video_tasks: dict[str, dict] = {}


# ── 请求/响应模型 ─────────────────────────────────────
class ImageRequest(BaseModel):
    prompt: str
    size: str = "1024x1024"
    quality: str = "standard"
    n: int = 1


class ImageResponse(BaseModel):
    url: str
    markdown: str


class VideoRequest(BaseModel):
    prompt: str
    size: str = "1080p"
    seconds: str = "5"
    image_url: Optional[str] = None   # 图生视频时传参考图 URL


class VideoSubmitResponse(BaseModel):
    task_id: str
    status: str
    message: str


class VideoStatusResponse(BaseModel):
    task_id: str
    status: str          # pending / processing / done / failed
    url: Optional[str] = None
    markdown: Optional[str] = None
    error: Optional[str] = None


# ── 七牛云存储 ────────────────────────────────────────
def upload_to_qiniu(data: bytes, filename: str) -> str:
    """上传字节到七牛，返回公开访问 URL"""
    q = Auth(QINIU_ACCESS_KEY, QINIU_SECRET_KEY)
    token = q.upload_token(QINIU_BUCKET, filename, 3600)
    ret, info = put_data(token, filename, data, hostscache_dir='/tmp')
    if info.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Qiniu upload failed: {info}")
    domain = QINIU_DOMAIN.rstrip("/")
    return f"http://{domain}/{filename}"


def upload_base64_to_qiniu(b64_data: str, filename: str) -> str:
    return upload_to_qiniu(base64.b64decode(b64_data), filename)


# ── AIHubMix 图片 ─────────────────────────────────────
async def call_image_api(prompt: str, size: str, quality: str, n: int) -> list[str]:
    headers = {
        "Authorization": f"Bearer {AIHUBMIX_API_KEY}",
        "Content-Type": "application/json",
    }

    # Gemini 走原生 streamGenerateContent 接口
    if "gemini" in IMAGE_MODEL.lower():
        gemini_headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": AIHUBMIX_API_KEY,
        }
        gemini_payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": "1:1", "imageSize": "1k"},
            },
        }
        # 用非流式接口，更稳定
        url = f"https://aihubmix.com/gemini/v1beta/models/{IMAGE_MODEL}:generateContent"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=gemini_headers, json=gemini_payload)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code,
                                    detail=f"Gemini image error: {resp.text}")
            import json as _json
            data = resp.json()
            b64_list = []
            for cand in data.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    inline = part.get("inlineData", {})
                    if inline.get("data"):
                        b64_list.append(inline["data"])
            return b64_list

    # 其他模型（dall-e-3, flux 等）走标准 images/generations
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": n,
        "response_format": "b64_json",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{AIHUBMIX_BASE_URL}/images/generations",
            headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code,
                                detail=f"AIHubMix image error: {resp.text}")
        return [item["b64_json"] for item in resp.json()["data"]]


# ── AIHubMix 视频 ─────────────────────────────────────
async def submit_video_task(prompt: str, size: str, seconds: str,
                             image_url: Optional[str]) -> str:
    """提交视频生成，返回 video_id"""
    headers = {"Authorization": f"Bearer {AIHUBMIX_API_KEY}"}

    if image_url:
        # 图生视频：下载参考图后 multipart 上传
        async with httpx.AsyncClient(timeout=30) as client:
            img_resp = await client.get(image_url)
            img_bytes = img_resp.content
            img_ext = image_url.split(".")[-1].split("?")[0] or "jpg"

        files = {
            "prompt": (None, prompt),
            "model": (None, VIDEO_MODEL),
            "size": (None, size),
            "seconds": (None, seconds),
            "input_reference": (f"ref.{img_ext}", img_bytes, f"image/{img_ext}"),
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{AIHUBMIX_BASE_URL}/videos",
                                     headers=headers, files=files)
    else:
        # 文生视频
        headers["Content-Type"] = "application/json"
        payload = {
            "model": VIDEO_MODEL,
            "prompt": prompt,
            "size": size,
            "seconds": seconds,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{AIHUBMIX_BASE_URL}/videos",
                                     headers=headers, json=payload)

    if resp.status_code not in (200, 201, 202):
        raise HTTPException(status_code=resp.status_code,
                            detail=f"AIHubMix video submit error: {resp.text}")

    data = resp.json()
    video_id = (data.get("id") or data.get("video_id") or data.get("task_id"))
    if not video_id:
        raise HTTPException(status_code=500,
                            detail=f"No video_id in response: {data}")
    return str(video_id)


async def poll_and_upload(task_id: str, video_id: str):
    """后台轮询视频，完成后下载上传七牛"""
    headers = {"Authorization": f"Bearer {AIHUBMIX_API_KEY}"}
    max_wait = 600   # 最多等 10 分钟
    interval = 10    # 每 10 秒查一次

    for _ in range(max_wait // interval):
        await asyncio.sleep(interval)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                status_resp = await client.get(
                    f"{AIHUBMIX_BASE_URL}/videos/{video_id}",
                    headers=headers)

            if status_resp.status_code == 200:
                status_data = status_resp.json()
                st = (status_data.get("status") or "").lower()

                if st in ("succeeded", "completed", "done", "success"):
                    # 先看有没有直接 URL
                    direct_url = (
                        status_data.get("url")
                        or status_data.get("video_url")
                        or (status_data.get("output") or {}).get("url")
                        or ""
                    )

                    if direct_url:
                        async with httpx.AsyncClient(timeout=120,
                                                      follow_redirects=True) as dl:
                            dl_resp = await dl.get(direct_url)
                        video_bytes = dl_resp.content
                    else:
                        # 没有 URL，走 /content 下载
                        async with httpx.AsyncClient(timeout=120,
                                                      follow_redirects=True) as dl:
                            dl_resp = await dl.get(
                                f"{AIHUBMIX_BASE_URL}/videos/{video_id}/content",
                                headers=headers)
                        if dl_resp.status_code != 200:
                            video_tasks[task_id] = {
                                "status": "failed",
                                "error": f"Download failed: {dl_resp.status_code}"}
                            return
                        video_bytes = dl_resp.content

                    filename = f"ai-videos/{uuid.uuid4().hex}.mp4"
                    oss_url = upload_to_qiniu(video_bytes, filename)
                    video_tasks[task_id] = {
                        "status": "done",
                        "url": oss_url,
                        "markdown": f"[▶ 点击播放视频]({oss_url})",
                    }
                    return

                elif st in ("failed", "error"):
                    video_tasks[task_id] = {
                        "status": "failed",
                        "error": status_data.get("error", "Generation failed")}
                    return
                # 还在处理，继续等

        except Exception as e:
            print(f"Poll error for {task_id}: {e}")

    video_tasks[task_id] = {"status": "failed", "error": "Timeout after 10 minutes"}


# ── 路由 ─────────────────────────────────────────────
def check_token(token: str):
    if API_TOKEN and token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid API token")


@app.get("/health")
def health():
    return {"status": "ok", "tasks": len(video_tasks)}


# 图片生成（同步）
@app.post("/generate-image", response_model=ImageResponse)
async def generate_image(
    req: ImageRequest,
    x_api_token: str = Header(default=""),
):
    check_token(x_api_token)
    b64_list = await call_image_api(req.prompt, req.size, req.quality, req.n)
    if not b64_list:
        raise HTTPException(status_code=500, detail="No image returned")
    filename = f"ai-images/{uuid.uuid4().hex}.png"
    url = upload_base64_to_qiniu(b64_list[0], filename)
    return ImageResponse(url=url, markdown=f"![generated]({url})")


# 视频提交（异步）→ 返回 task_id
@app.post("/generate-video", response_model=VideoSubmitResponse)
async def generate_video(
    req: VideoRequest,
    background_tasks: BackgroundTasks,
    x_api_token: str = Header(default=""),
):
    check_token(x_api_token)
    video_id = await submit_video_task(
        req.prompt, req.size, req.seconds, req.image_url)

    task_id = f"task_{uuid.uuid4().hex[:8]}"
    video_tasks[task_id] = {"status": "processing"}
    background_tasks.add_task(poll_and_upload, task_id, video_id)

    return VideoSubmitResponse(
        task_id=task_id,
        status="processing",
        message="视频生成中，请用 task_id 轮询 /video-status/{task_id}",
    )


# 查询视频状态
@app.get("/video-status/{task_id}", response_model=VideoStatusResponse)
async def video_status(
    task_id: str,
    x_api_token: str = Header(default=""),
):
    check_token(x_api_token)
    task = video_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return VideoStatusResponse(
        task_id=task_id,
        status=task["status"],
        url=task.get("url"),
        markdown=task.get("markdown"),
        error=task.get("error"),
    )
