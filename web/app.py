#!/usr/bin/env python3
"""dyTextExtractor WebUI

启动方式:
    cd dyTextExtractor
    uv run python web/app.py
    # 访问 http://localhost:8080
"""

import asyncio
import hashlib
import json
import sys
import threading
from pathlib import Path

# 把项目根目录加入路径，保证以任意目录启动都能 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn

from dy_extractor import douyin, pipeline
from dy_extractor.ai_format import LLMError, build_formatted_file, format_transcript, read_formatted_file
from dy_extractor.pipeline import formatted_filename, subtitles_filename, transcript_filename
from dy_extractor.config import deepseek_configured, get_api_key, PROJECT_ROOT
from dy_extractor.doubao_asr import DoubaoASRError

OUTPUT_DIR = PROJECT_ROOT / "output"

app = FastAPI(title="dyTextExtractor - 抖音文案提取器", version="1.0.0")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# 静态资源（favicon 等）
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class VideoRequest(BaseModel):
    """视频请求模型"""
    url: str
    language: str = ""  # 识别语言：auto/zh-CN/en-US；留空则用配置默认（auto）


class FormatRequest(BaseModel):
    """AI 排版请求模型（video_id/language 用于定位排版产物缓存）"""
    text: str
    video_id: str = ""
    language: str = ""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页面。

    加 Cache-Control: no-store 防止浏览器缓存旧 HTML——
    页面随每次部署更新，若被缓存会导致「新功能看不到」的假性 bug。
    """
    return templates.TemplateResponse(
        request,
        "index.html",
        {},
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/health")
async def health_check():
    """健康检查：返回各 API Key 是否已配置"""
    try:
        get_api_key()
        configured = True
    except RuntimeError:
        configured = False
    return {
        "status": "ok",
        "api_key_configured": configured,
        "deepseek_configured": deepseek_configured(),
    }


@app.post("/api/video/info")
async def get_info(req: VideoRequest):
    """获取视频信息（不下载，无需 API Key）"""
    try:
        info = await asyncio.to_thread(douyin.parse_share_url, req.url)
        return {
            "success": True,
            "video_id": info.video_id,
            "title": info.title,
            "download_url": info.url,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/video/extract")
async def extract_transcript(req: VideoRequest):
    """完整流程：下载视频 + 提取音频 + 豆包识别文案 + 保存"""
    try:
        result = await asyncio.to_thread(
            pipeline.extract, req.url, str(OUTPUT_DIR), None, req.language
        )
        return {
            "success": True,
            "video_id": result["video_info"].video_id,
            "title": result["video_info"].title,
            "text": result["text"],
            "from_cache": result["from_cache"],
        }
    except DoubaoASRError as e:
        return {"success": False, "code": e.code, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"提取失败：{e}"}


def _sse(item: dict) -> str:
    """把事件序列化为 SSE data 行。"""
    return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"


@app.post("/api/extract/stream")
async def extract_stream(req: VideoRequest):
    """流式提取：SSE 推送各里程碑事件。

    事件（data 中的 stage 字段）：
        parse        → 开始解析
        video_ready  → 视频已下载，前端可立即播放
        audio_ready  → 音频已提取，前端可立即播放
        done         → 识别完成（携带 text / from_cache）
        error        → 失败（携带 message / code）
    视频/音频一就绪就推给前端，不必等 ASR 全部完成。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def progress(stage: str, data: dict):
        loop.call_soon_threadsafe(queue.put_nowait, {"stage": stage, **data})

    def worker():
        try:
            pipeline.extract(req.url, str(OUTPUT_DIR), progress=progress, language=req.language)
            loop.call_soon_threadsafe(queue.put_nowait, {"stage": "end"})
        except Exception as e:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"stage": "error", "message": str(e), "code": getattr(e, "code", "")},
            )

    threading.Thread(target=worker, daemon=True).start()

    async def event_generator():
        # 首个事件由 pipeline 的 emit("parse") 产生，这里不重复发
        while True:
            item = await queue.get()
            if item["stage"] == "end":
                break
            yield _sse(item)
            if item["stage"] == "error":
                break  # 失败后立即结束流，避免前端一直等待

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/format")
async def format_text(req: FormatRequest):
    """AI 排版：用 DeepSeek V4 Flash 整理口播文案（只排版不改写）。

    排版结果是来源文案的确定性派生，落盘到 output/{video_id}/formatted_{lang}.md
    （首行带来源 hash）。同文案再次排版直接命中磁盘缓存 from_cache=True，
    不重复调用 DeepSeek；来源 hash 不匹配（文案已变）则自动重排覆盖。
    """
    if not req.text.strip():
        return {"success": False, "error": "文案为空，无法排版"}
    if not deepseek_configured():
        return {"success": False, "error": "未配置 DEEPSEEK_API_KEY，请在 .env 中填写后重启服务"}

    # 缓存定位：带来源 hash 的排版产物（video_id 非法或缺失则不缓存）
    key = hashlib.md5(req.text.encode("utf-8")).hexdigest()
    cache_path = None
    if req.video_id and req.video_id.isdigit():
        cache_path = OUTPUT_DIR / req.video_id / formatted_filename(req.language)

    if cache_path is not None and cache_path.is_file() and cache_path.stat().st_size > 0:
        body, stored_hash = read_formatted_file(cache_path.read_text(encoding="utf-8"))
        if stored_hash == key:
            return {"success": True, "text": body, "from_cache": True}

    try:
        formatted = await asyncio.to_thread(format_transcript, req.text)
    except LLMError as e:
        return {"success": False, "code": e.code, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"排版失败：{e}"}

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(build_formatted_file(req.text, formatted), encoding="utf-8")
    return {"success": True, "text": formatted, "from_cache": False}


# 允许下载的文件名白名单（语言相关的文案/字幕走 language 参数）
_SAFE_FILES = {"video.mp4", "audio.mp3", "transcript.md", "subtitles.srt"}
# 允许内联播放的文件
_MEDIA_FILES = {"video.mp4", "audio.mp3"}


def _resolve_output_file(video_id: str, file: str) -> Path | None:
    """解析产物文件的真实路径（按已知命名逐级回退）。

    1. 现行约定：video.mp4 / audio.mp3 / transcript.md
    2. 历史 bug：音频曾被按视频名派生为 video.mp3
    3. 命名统一前的存量数据：{video_id}.mp4 / .mp3
    """
    folder = OUTPUT_DIR / video_id
    candidates = [file]
    if file == "audio.mp3":
        candidates.append("video.mp3")  # 历史 bug 产物
    candidates.append(f"{video_id}{Path(file).suffix}")  # 命名统一前存量

    for name in candidates:
        path = folder / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _resolve_subtitles_file(video_id: str, language: str = "") -> Path | None:
    """解析字幕产物路径：显式语言优先，其次 auto；无存量命名无需 legacy 回退。"""
    folder = OUTPUT_DIR / video_id
    names = []
    if language:
        names.append(subtitles_filename(language))
    names.append(subtitles_filename("auto"))

    for name in names:
        path = folder / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _resolve_transcript_file(video_id: str, language: str = "") -> Path | None:
    """解析文案产物路径（按识别语言 + 存量命名逐级回退）。

    1. 现行约定：transcript_{lang}.md（用户显式选择的语言优先）
    2. 语言功能前默认识别产物：transcript.md
    3. 命名统一前的存量数据：{video_id}.md
    """
    folder = OUTPUT_DIR / video_id
    names = []
    if language:
        names.append(transcript_filename(language))
    names.append(transcript_filename("auto"))
    names += ["transcript.md", f"{video_id}.md"]

    for name in names:
        path = folder / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


@app.get("/api/download")
async def download_file(video_id: str, file: str, language: str = ""):
    """下载提取产物：video.mp4 / audio.mp3 / transcript.md / subtitles.srt（文案与字幕按识别语言）"""
    if not video_id.isdigit():
        raise HTTPException(status_code=400, detail="无效的视频 ID")
    if file not in _SAFE_FILES:
        raise HTTPException(status_code=400, detail="无效的文件名")

    if file == "transcript.md":
        path = _resolve_transcript_file(video_id, language)
    elif file == "subtitles.srt":
        path = _resolve_subtitles_file(video_id, language)
    else:
        path = _resolve_output_file(video_id, file)
    if path is None:
        raise HTTPException(status_code=404, detail="文件不存在，请先提取文案")

    return FileResponse(path, filename=f"{video_id}_{file}")


@app.get("/api/media")
async def media_file(video_id: str, file: str):
    """内联提供视频/音频用于页面播放（不带下载头，浏览器可直接播放）"""
    if not video_id.isdigit():
        raise HTTPException(status_code=400, detail="无效的视频 ID")
    if file not in _MEDIA_FILES:
        raise HTTPException(status_code=400, detail="无效的文件名")

    path = _resolve_output_file(video_id, file)
    if path is None:
        raise HTTPException(status_code=404, detail="文件不存在，请先提取文案")

    media_type = "video/mp4" if file == "video.mp4" else "audio/mpeg"
    return FileResponse(path, media_type=media_type)


def main():
    """启动服务"""
    host = "127.0.0.1"
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print("🚀 dyTextExtractor WebUI 已启动: http://localhost:%d" % port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
