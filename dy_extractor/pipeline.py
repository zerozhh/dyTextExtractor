"""串联完整流程：分享链接 → 无水印视频 + 音频 + 文案。

第一性原理：同一直视频的产出是确定性的，output/{video_id}/ 天然就是按视频 ID
做的缓存。因此：
- 解析后先检查文案是否已存在 → 命中则直接返回历史结果（不再下载/识别）
- 各步骤幂等：产物文件已存在且非空则跳过，支持失败后断点续跑
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import audio, doubao_asr, douyin
from .config import get_api_key, get_language, get_resource_id
from .media import media_is_complete

# transcript.md 中文案正文的起始标记
_CONTENT_MARKER = "## 文案内容\n\n"


def _format_transcript(info: douyin.VideoInfo, text: str) -> str:
    return (
        f"# {info.title}\n\n"
        f"| 属性 | 值 |\n"
        f"|------|----|\n"
        f"| 视频ID | `{info.video_id}` |\n"
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
    info = douyin.parse_share_url(share_link)

    out = Path(output_dir) / info.video_id
    out.mkdir(parents=True, exist_ok=True)

    video_path = out / "video.mp4"
    audio_path = out / "audio.mp3"
    transcript_path = out / "transcript.md"

    # === 缓存命中：文案已存在。但缓存只在源视频完整时可信任 ===
    if _is_valid(transcript_path):
        if _video_usable(video_path):
            text = _extract_text_body(transcript_path.read_text(encoding="utf-8"))
            print(f"⚡ 命中历史记录，直接返回: {out}")
            # 产物文件已在历史目录里（可能为旧命名），仍通知前端可播放
            emit("video_ready", video_id=info.video_id, title=info.title)
            emit("audio_ready", video_id=info.video_id)
            emit("done", video_id=info.video_id, title=info.title, text=text, from_cache=True)
            return {
                "video_info": info,
                "video_path": str(video_path),
                "audio_path": str(audio_path),
                "transcript_path": str(transcript_path),
                "text": text,
                "from_cache": True,
            }
        # 源视频残缺 → 整份缓存（文案、音频）都是基于残缺内容派生的，作废重跑
        print(f"⚠️ 缓存中的视频不完整（{video_path.name}），作废整份缓存重新提取: {out}")
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)

    # === 幂等：已有且完整则跳过，支持断点续跑 ===
    if _video_usable(video_path):
        print(f"② 已存在完整视频，跳过下载: {video_path.name}")
    else:
        print(f"② 下载无水印视频: {info.title}")
        # 视频将重新下载 → 它派生的音频/文案全部失效，一并清掉，避免张冠李戴
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
    text = doubao_asr.transcribe(api_key, audio_path, language, resource_id)

    print("⑤ 保存文案...")
    transcript_path.write_text(_format_transcript(info, text), encoding="utf-8")

    emit("done", video_id=info.video_id, title=info.title, text=text, from_cache=False)
    print(f"✅ 完成，输出目录: {out}")
    return {
        "video_info": info,
        "video_path": str(video_path),
        "audio_path": str(audio_path),
        "transcript_path": str(transcript_path),
        "text": text,
        "from_cache": False,
    }
