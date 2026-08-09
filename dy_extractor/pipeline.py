"""串联完整流程：分享链接 → 无水印视频 + 音频 + 文案。

第一性原理：同一直视频的产出是确定性的，output/{video_id}/ 天然就是按视频 ID
做的缓存。因此：
- 解析后先检查文案是否已存在 → 命中则直接返回历史结果（不再下载/识别）
- 文案按识别语言分文件缓存（transcript_{lang}.md），语言不同不互相覆盖
- 链接→视频信息的映射持久化到 .url_index.json，重复链接命中缓存时零网络解析，
  抖音改版解析器失效也不拖垮历史缓存
- 各步骤幂等：产物文件已存在且非空则跳过，支持失败后断点续跑
"""

import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import audio, doubao_asr, douyin
from .config import get_api_key, get_language, get_resource_id
from .media import media_is_complete
from .subtitles import to_srt

# transcript.md 中文案正文的起始标记
_CONTENT_MARKER = "## 文案内容\n\n"

# 本地链接解析缓存：{分享链接: 视频信息}，让重复链接命中缓存时零网络解析
_URL_INDEX_NAME = ".url_index.json"

# 语言值会用作文件名（如 transcript_zh-CN.md），清洗掉路径非法字符
_SAFE_FILE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


def transcript_filename(language: str) -> str:
    """按识别语言返回文案缓存文件名：transcript_auto.md / transcript_zh-CN.md。"""
    safe = _SAFE_FILE_CHARS.sub("_", (language or "auto").strip()).strip("._")
    return f"transcript_{safe or 'auto'}.md"


def subtitles_filename(language: str) -> str:
    """按识别语言返回字幕文件名：subtitles_auto.srt / subtitles_zh-CN.srt。"""
    safe = _SAFE_FILE_CHARS.sub("_", (language or "auto").strip()).strip("._")
    return f"subtitles_{safe or 'auto'}.srt"


def formatted_filename(language: str) -> str:
    """按识别语言返回 AI 排版产物文件名：formatted_auto.md / formatted_zh-CN.md。"""
    safe = _SAFE_FILE_CHARS.sub("_", (language or "auto").strip()).strip("._")
    return f"formatted_{safe or 'auto'}.md"


def _format_transcript(info: douyin.VideoInfo, text: str, language: str) -> str:
    return (
        f"# {info.title}\n\n"
        f"| 属性 | 值 |\n"
        f"|------|----|\n"
        f"| 视频ID | `{info.video_id}` |\n"
        f"| 识别语言 | {language} |\n"
        f"| 提取时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n"
        f"| 下载链接 | [点击下载]({info.url}) |\n\n"
        f"---\n\n"
        f"## 文案内容\n\n{text}\n"
    )


def _extract_text_body(transcript_md: str) -> str:
    """从已保存的 transcript.md 中提取纯文案正文。"""
    if _CONTENT_MARKER in transcript_md:
        return transcript_md.split(_CONTENT_MARKER, 1)[1].strip()
    return transcript_md.strip()


def _is_valid(path: Path) -> bool:
    """产物文件是否存在且非空（非空避免误用中断下载产生的残文件）。"""
    return path.exists() and path.stat().st_size > 0


def _video_usable(path: Path) -> bool:
    """视频可用的判定：非空 且 完整性校验通过。

    第一性原理：非空 ≠ 完整。截断的 mp4（moov 声明完整时长、mdat 只有前段）
    大小 > 0 却播到一半就坏，必须由 media_is_complete 拦下。
    """
    return _is_valid(path) and media_is_complete(path)


class _UrlIndex:
    """本地链接解析缓存：{分享链接 → 视频信息}。

    第一性原理：产物缓存键是 video_id，但拿到 video_id 依赖「联网解析抖音页面」
    这个脆弱步骤。把「链接 → 视频信息」的映射持久化到 .url_index.json，重复链接
    就不再需要联网解析——抖音改版只影响新链接，不拖垮历史缓存。
    """

    def __init__(self, output_dir: str):
        self.path = Path(output_dir) / _URL_INDEX_NAME
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def lookup(self, share_text: str) -> Optional[douyin.VideoInfo]:
        """按分享文本查本地映射；未命中返回 None（纯本地，不联网）。"""
        url = douyin.extract_share_url(share_text)
        if not url:
            return None
        entry = self._read().get(url)
        if not entry:
            return None
        return douyin.VideoInfo(
            video_id=str(entry["video_id"]),
            title=str(entry.get("title", "")),
            url=str(entry.get("url", "")),
        )

    def record(self, share_text: str, info: douyin.VideoInfo) -> None:
        """把 链接→视频信息 写入索引（原子替换 + 加锁，线程安全）。"""
        url = douyin.extract_share_url(share_text)
        if not url:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = self._read()
            data[url] = {"video_id": info.video_id, "title": info.title, "url": info.url}
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)


def _resolve_info(share_link: str, output_dir: str) -> douyin.VideoInfo:
    """解析分享链接 → 视频信息。

    优先用本地链接索引：同一分享链接重复提取时零网络解析。索引仅在对应产物目录
    已存在时才被信任——避免用过期直链去下载从未处理过的新视频。
    """
    index = _UrlIndex(output_dir)
    cached = index.lookup(share_link)
    if cached is not None:
        out = Path(output_dir) / cached.video_id
        if out.is_dir() and any(out.iterdir()):
            print(f"⚡ 命中链接解析缓存，跳过网络解析: {out.name}")
            return cached
    info = douyin.parse_share_url(share_link)
    index.record(share_link, info)
    return info


def extract(
    share_link: str,
    output_dir: str = "output",
    progress: Optional[Callable] = None,
    language: str = "",
) -> dict:
    """处理一个分享链接，保存视频/音频/文案到 output/{video_id}/。

    返回 dict，包含各产物路径与识别文本，以及是否命中历史缓存 from_cache。

    progress: 可选回调 progress(stage, data)，在每个里程碑完成时被调用：
        - parse       开始解析
        - video_ready 视频下载完成（可用 /api/media 播放）
        - audio_ready 音频提取完成（可用 /api/media 播放）
        - done        识别完成（携带 text / from_cache）
    用于 WebUI 渐进式展示：视频/音频一就绪就推给前端，不必等 ASR 全部完成。

    language: 识别语种，auto=自动识别 / zh-CN=中文 / en-US=英文 等。
        传入非空值则优先使用；留空则回退到配置（DOUBAO_LANGUAGE，默认 auto）。
        缓存按 (video_id, language) 键控：同一视频不同语言各存一份，互不覆盖。
    """
    def emit(stage: str, **data):
        if progress:
            progress(stage, data)

    api_key = get_api_key()
    if not language:
        language = get_language()  # 未显式指定时回退到配置（默认 auto）
    resource_id = get_resource_id()

    emit("parse")
    print("① 解析分享链接...")
    info = _resolve_info(share_link, output_dir)

    out = Path(output_dir) / info.video_id
    out.mkdir(parents=True, exist_ok=True)

    video_path = out / "video.mp4"
    audio_path = out / "audio.mp3"
    # 文案与字幕按识别语言分文件：transcript_auto.md / subtitles_zh-CN.srt ...
    transcript_path = out / transcript_filename(language)
    subtitles_path = out / subtitles_filename(language)
    # 语言功能上线前的存量命名；当时没有语言选项，都是默认识别，仅 auto 可命中
    legacy_path = out / "transcript.md"

    # === 缓存命中：本语言的文案已存在。但缓存只在源视频完整时可信任 ===
    cached_transcript = transcript_path if _is_valid(transcript_path) else None
    if cached_transcript is None and language == "auto":
        cached_transcript = legacy_path if _is_valid(legacy_path) else None

    if cached_transcript is not None:
        if _video_usable(video_path):
            text = _extract_text_body(cached_transcript.read_text(encoding="utf-8"))
            # 存量数据（语言功能前）没有时间轴，无法凭空生成字幕 → 如实报 False
            has_subtitles = _is_valid(subtitles_path)
            print(f"⚡ 命中历史记录，直接返回: {out}")
            # 产物文件已在历史目录里（可能为旧命名），仍通知前端可播放
            emit("video_ready", video_id=info.video_id, title=info.title)
            emit("audio_ready", video_id=info.video_id)
            emit("done", video_id=info.video_id, title=info.title, text=text,
                 from_cache=True, has_subtitles=has_subtitles)
            return {
                "video_info": info,
                "video_path": str(video_path),
                "audio_path": str(audio_path),
                "transcript_path": str(cached_transcript),
                "subtitles_path": str(subtitles_path),
                "text": text,
                "from_cache": True,
                "has_subtitles": has_subtitles,
            }
        # 源视频残缺 → 整份缓存（各语言文案/字幕、音频）都是基于残缺内容派生的，作废重跑
        print(f"⚠️ 缓存中的视频不完整（{video_path.name}），作废整份缓存重新提取: {out}")
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)

    # === 幂等：已有且完整则跳过，支持断点续跑 ===
    if _video_usable(video_path):
        print(f"② 已存在完整视频，跳过下载: {video_path.name}")
    else:
        print(f"② 下载无水印视频: {info.title}")
        # 视频将重新下载 → 它派生的音频与所有语言文案/字幕全部失效，一并清掉，避免张冠李戴
        for p in out.glob("transcript*.md"):
            p.unlink(missing_ok=True)
        for p in out.glob("subtitles*.srt"):
            p.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)
        video_path.unlink(missing_ok=True)
        video_path = douyin.download_video(info, out)
        if not media_is_complete(video_path):
            video_path.unlink(missing_ok=True)
            raise RuntimeError("下载后的视频仍校验不完整，已删除，请重试该链接")
    emit("video_ready", video_id=info.video_id, title=info.title)

    if _is_valid(audio_path):
        print(f"③ 已存在音频，跳过提取: {audio_path.name}")
    else:
        print("③ 用 FFmpeg 提取音频...")
        audio_path = audio.extract_audio(video_path, out)
    emit("audio_ready", video_id=info.video_id)

    print(f"④ 豆包语音识别中（语言: {language}，通常几十秒）...")
    asr = doubao_asr.transcribe(api_key, audio_path, language, resource_id)

    # 文案与字幕是同一次识别的两个视图，一次落盘两个产物
    print("⑤ 保存文案与字幕...")
    transcript_path.write_text(_format_transcript(info, asr.text, language), encoding="utf-8")
    if asr.utterances:
        subtitles_path.write_text(to_srt(asr.utterances), encoding="utf-8")
    has_subtitles = bool(asr.utterances)

    emit("done", video_id=info.video_id, title=info.title, text=asr.text,
         from_cache=False, has_subtitles=has_subtitles)
    print(f"✅ 完成，输出目录: {out}")
    return {
        "video_info": info,
        "video_path": str(video_path),
        "audio_path": str(audio_path),
        "transcript_path": str(transcript_path),
        "subtitles_path": str(subtitles_path),
        "text": asr.text,
        "from_cache": False,
        "has_subtitles": has_subtitles,
    }
