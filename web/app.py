#!/usr/bin/env python3
"""dyTextExtractor WebUI

启动方式:
    cd dyTextExtractor
    uv run python web/app.py            # 默认 0.0.0.0:8080，局域网可访问
    uv run python web/app.py 127.0.0.1  # 只允许本机访问
    uv run python web/app.py 0.0.0.0 9000  # 指定 host 和 port
"""

import asyncio
import hashlib
import json
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

# 把项目根目录加入路径，保证以任意目录启动都能 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
import uvicorn

from dy_extractor import douyin, manifest, pipeline
from dy_extractor.ai_format import LLMError, build_formatted_file, format_transcript, read_formatted_file
from dy_extractor.pipeline import formatted_filename, subtitles_filename, transcript_filename, url_index_lock
from dy_extractor.config import deepseek_configured, get_api_key, PROJECT_ROOT
from dy_extractor.doubao_asr import DoubaoASRError

OUTPUT_DIR = PROJECT_ROOT / "output"

app = FastAPI(title="dyTextExtractor - 抖音文案提取器", version="1.0.0")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# 静态资源（favicon 等）
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class VideoRequest(BaseModel):
    """视频请求模型"""
    url: str = Field(max_length=4096)  # 上限防超大 body DoS
    language: str = Field(default="", max_length=64)  # 识别语言：auto/zh-CN/en-US；留空则用配置默认（auto）


class FormatRequest(BaseModel):
    """AI 排版请求模型（video_id/language 用于定位排版产物缓存）"""
    text: str = Field(max_length=1_000_000)  # 文案上限，防超大 body DoS
    video_id: str = Field(default="", max_length=32)
    language: str = Field(default="", max_length=64)


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

    # 缓存定位：带来源 hash 的排版产物（video_id 目录不存在则不缓存——
    # 防止任意数字 video_id 无授权建目录污染历史列表）
    key = hashlib.md5(req.text.encode("utf-8")).hexdigest()
    cache_path = None
    format_folder = None
    if req.video_id and req.video_id.isdigit():
        format_folder = OUTPUT_DIR / req.video_id
        if format_folder.is_dir() and not format_folder.is_symlink():
            cache_path = manifest.resolve(format_folder, formatted_filename(req.language))

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
        # 写入前走 manifest：若用户改过 formatted 名，reset 后写回逻辑名保持一致
        manifest.reset_logic_file(format_folder, formatted_filename(req.language))
        cache_path = manifest.resolve(format_folder, formatted_filename(req.language))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(build_formatted_file(req.text, formatted), encoding="utf-8")
    return {"success": True, "text": formatted, "from_cache": False}


# 允许下载的文件名白名单（语言相关的文案/字幕走 language 参数）
_SAFE_FILES = {"video.mp4", "audio.mp3", "transcript.md", "subtitles.srt"}
# 允许内联播放的文件
_MEDIA_FILES = {"video.mp4", "audio.mp3"}


def _resolve_output_file(video_id: str, file: str) -> Path | None:
    """解析产物文件的真实路径：经逻辑层（manifest 记录用户改名）定位。"""
    folder = OUTPUT_DIR / video_id
    if not folder.is_dir():
        return None
    path = manifest.resolve(folder, file)
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def _resolve_subtitles_file(video_id: str, language: str = "") -> Path | None:
    """解析字幕产物路径：显式语言优先，其次 auto；再兜底 manifest 任意语言字幕
    （改名后语言不可推断时，保证历史页下载不 404）。"""
    folder = OUTPUT_DIR / video_id
    if not folder.is_dir():
        return None
    names = []
    if language:
        names.append(subtitles_filename(language))
    names.append(subtitles_filename("auto"))

    for name in names:
        path = manifest.resolve(folder, name)
        if path.is_file() and path.stat().st_size > 0:
            return path
    for logic, actual in manifest.read_manifest(folder).items():
        if logic.startswith("subtitles_"):
            path = folder / actual
            if path.is_file() and path.stat().st_size > 0:
                return path
    return None


def _resolve_transcript_file(video_id: str, language: str = "") -> Path | None:
    """解析文案产物路径：显式语言优先，其次 auto；再兜底 manifest 任意语言文案
    （改名后语言不可推断时，保证历史页下载不 404）。"""
    folder = OUTPUT_DIR / video_id
    if not folder.is_dir():
        return None
    names = []
    if language:
        names.append(transcript_filename(language))
    names.append(transcript_filename("auto"))

    for name in names:
        path = manifest.resolve(folder, name)
        if path.is_file() and path.stat().st_size > 0:
            return path
    for logic, actual in manifest.read_manifest(folder).items():
        if logic.startswith("transcript_"):
            path = folder / actual
            if path.is_file() and path.stat().st_size > 0:
                return path
    return None


def _resolve_formatted_file(video_id: str, language: str = "") -> Path | None:
    """解析 AI 排版产物路径：语言优先，其次 auto；再兜底 manifest 任意 formatted（改名可定位）。"""
    folder = OUTPUT_DIR / video_id
    if not folder.is_dir():
        return None
    name = formatted_filename(language or "auto")
    path = manifest.resolve(folder, name)
    if path.is_file() and path.stat().st_size > 0:
        return path
    for logic, actual in manifest.read_manifest(folder).items():
        if logic.startswith("formatted_"):
            path = folder / actual
            if path.is_file() and path.stat().st_size > 0:
                return path
    return None


# 允许网页内预览的文本产物（文案/字幕/AI排版）
_SAFE_TEXT_FILES = {"transcript.md", "subtitles.srt", "formatted.md"}


@app.get("/api/content")
async def content_file(video_id: str, file: str, language: str = ""):
    """文本产物内容预览：文案/字幕/AI排版在网页内直接查看，无需下载。"""
    if not video_id.isdigit():
        raise HTTPException(status_code=400, detail="无效的视频 ID")
    if file not in _SAFE_TEXT_FILES:
        raise HTTPException(status_code=400, detail="无效的文件名")
    if file == "transcript.md":
        path = _resolve_transcript_file(video_id, language)
    elif file == "subtitles.srt":
        path = _resolve_subtitles_file(video_id, language)
    else:
        path = _resolve_formatted_file(video_id, language)
    if path is None:
        raise HTTPException(status_code=404, detail="文件不存在，请先提取文案")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise HTTPException(status_code=500, detail="读取失败")
    return {"success": True, "name": path.name, "text": text}


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


# ===== 历史记录：WebUI 内回看与管理 =====
# 第一性原理：查找应发生在网页内，而非翻文件系统。数据源直接扫描 output/
# 数字目录（磁盘为准），标题/链接从 .url_index.json 反查。产物清单展示磁盘
# 实际文件名；可改名文件附带逻辑名（经 manifest 反查）供改名入口用。


class RenameRequest(BaseModel):
    """历史产物改名请求：name 为当前实际文件名，newName 为新文件名。"""
    name: str
    newName: str


def _load_title_index() -> dict:
    """一次性读 .url_index.json → {video_id: (title, share_url)}。

    避免逐记录全量读+遍历（O(n²)），供历史扫描复用。
    """
    lookup = {}
    try:
        data = json.loads((OUTPUT_DIR / ".url_index.json").read_text(encoding="utf-8"))
        for url, v in data.items():
            lookup[str(v.get("video_id"))] = (str(v.get("title", "")), str(url))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return lookup


def _infer_language(files: list[dict]) -> str:
    """从目录内 transcript_*.md 推断识别语言；语言功能前的 transcript.md 视为 auto。"""
    for f in files:
        m = re.match(r"^transcript_(.+)\.md$", f["name"])
        if m:
            return m.group(1)
    if any(f["name"] == "transcript.md" for f in files):
        return "auto"
    return ""


def _product_files(folder: Path):
    """枚举产物目录内的交付文件：跳过隐藏（.files.json 等内部态）、目录、symlink。

    历史列表（_history_record）与一键打包共用——「打包的就是列表展示的集合」。
    """
    for p in folder.iterdir():
        if p.name.startswith(".") or not p.is_file() or p.is_symlink():
            continue
        yield p


def _history_record(d: Path, lookup: dict) -> dict:
    """把一个 output 目录转成历史记录条目（产物清单 + 元数据）。"""
    vid = d.name
    title, share_url = lookup.get(vid, ("", ""))
    files = []
    for p in _product_files(d):
        try:
            size = p.stat().st_size
        except OSError:
            continue  # 扫描竞态（并发删除/无权限），跳过该文件
        files.append({
            "name": p.name,
            "size": size,
            "logic": manifest.logic_name_for(d, p.name),  # 可改名文件的逻辑名，否则 None
        })
    files.sort(key=lambda f: f["name"])
    try:
        time = int(d.stat().st_mtime)
    except OSError:
        time = 0
    return {
        "video_id": vid,
        "title": title,
        "share_url": share_url,
        "language": _infer_language(files),
        "time": time,
        "files": files,
    }


def _remove_from_url_index(video_id: str) -> None:
    """删除目录后同步清掉 .url_index.json 中该视频的链接条目（原子写）。

    与 pipeline 的 _UrlIndex.record 共用全局锁，防并发写互踩/丢更新。
    """
    path = OUTPUT_DIR / ".url_index.json"
    with url_index_lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        cleaned = {url: v for url, v in data.items() if str(v.get("video_id")) != str(video_id)}
        if len(cleaned) != len(data):
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    """历史记录页：禁缓存，避免浏览器缓存旧页面。"""
    return templates.TemplateResponse(
        request,
        "history.html",
        {},
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/history")
def list_history():
    """历史记录：扫描 output/ 数字目录，按提取时间倒序。

    同步 def：FastAPI 自动放入线程池执行，避免阻塞事件循环（文件 IO 大时拖垮全服务）。
    """
    if not OUTPUT_DIR.is_dir():
        return {"success": True, "records": []}
    lookup = _load_title_index()
    records = []
    for d in OUTPUT_DIR.iterdir():
        if d.is_dir() and d.name.isdigit() and not d.is_symlink():
            records.append(_history_record(d, lookup))
    records.sort(key=lambda r: r["time"], reverse=True)
    return {"success": True, "records": records}


@app.delete("/api/history/{video_id}")
async def delete_history(video_id: str):
    """彻底删除：移除 output/{video_id}/ 并清 .url_index.json 对应条目。"""
    if not video_id.isdigit():
        raise HTTPException(status_code=400, detail="无效的视频 ID")
    folder = OUTPUT_DIR / video_id
    if not folder.exists():
        return {"success": True}
    if folder.is_symlink():
        raise HTTPException(status_code=400, detail="非法目录")  # 拒绝符号链接，防逃逸
    try:
        shutil.rmtree(folder)
    except OSError:
        raise HTTPException(status_code=500, detail="删除失败，请重试")
    _remove_from_url_index(video_id)
    return {"success": True}


@app.put("/api/history/{video_id}/rename")
async def rename_history_file(video_id: str, req: RenameRequest):
    """改名目录内产物（仅限逻辑文件；扩展名保留，后端权威校验）。"""
    if not video_id.isdigit():
        raise HTTPException(status_code=400, detail="无效的视频 ID")
    folder = OUTPUT_DIR / video_id
    if not folder.is_dir() or folder.is_symlink():
        raise HTTPException(status_code=404, detail="目录不存在")
    logic = manifest.logic_name_for(folder, req.name)
    if logic is None:
        raise HTTPException(status_code=400, detail="该文件不支持改名")
    try:
        path = manifest.rename_file(folder, logic, req.newName)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "name": path.name, "logic": logic}


# 文本产物用 deflate；mp4/mp3 已是高度压缩格式，STORED 避免重复压缩白耗 CPU
_ZIP_DEFLATE_EXT = {".md", ".srt", ".txt", ".json"}


def _zip_compression(p: Path) -> int:
    """按扩展名选 zip 压缩方式：文本 deflate，媒体 store。"""
    return ZIP_DEFLATED if p.suffix.lower() in _ZIP_DEFLATE_EXT else ZIP_STORED


@app.get("/api/history/{video_id}/download")
def download_history_zip(video_id: str):
    """一键打包：把一条历史记录的全部产物压缩为一个 zip 下载。

    第一性原理：详情页展示的产物集合 = 要交付的集合。zip 条目用磁盘实际名
    （改名后的名字即交付名）；纯只读，不改产物。同步 def：自动入线程池，
    大视频打包不阻塞事件循环。
    """
    if not video_id.isdigit():
        raise HTTPException(status_code=400, detail="无效的视频 ID")
    folder = OUTPUT_DIR / video_id
    if not folder.is_dir() or folder.is_symlink():
        raise HTTPException(status_code=404, detail="记录不存在")

    files = sorted(_product_files(folder), key=lambda p: p.name)
    if not files:
        raise HTTPException(status_code=404, detail="该记录没有可打包的文件")

    tmp = tempfile.NamedTemporaryFile(prefix=f"dz_{video_id}_", suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        with ZipFile(tmp_path, "w") as zf:
            for p in files:
                if not p.is_file():  # 并发改名/删除竞态：缺失就跳过，不中断打包
                    continue
                zf.write(p, arcname=p.name, compress_type=_zip_compression(p))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"{video_id}.zip",
        background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)),
    )


def main():
    """启动服务"""
    # 默认 0.0.0.0 监听所有网卡，局域网内其他设备可通过 http://<本机IP>:8080 访问
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    print("🚀 dyTextExtractor WebUI 已启动: http://localhost:%d （局域网访问: http://<本机IP>:%d）" % (port, port))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
