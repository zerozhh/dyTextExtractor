"""使用 FFmpeg 从视频中提取音频。

依赖系统已安装 ffmpeg 可执行文件（macOS: brew install ffmpeg）。
"""

from pathlib import Path

import ffmpeg


def extract_audio(video_path: Path, output_dir: Path) -> Path:
    """从视频文件提取 mp3 音频到输出目录，返回音频文件路径。

    文件名固定为 audio.mp3（与 WebUI 下载接口、README 约定一致），
    不要按视频文件名派生，否则会变成 video.mp3 导致前端 404。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / "audio.mp3"

    try:
        (
            ffmpeg.input(str(video_path))
            .output(str(audio_path), acodec="libmp3lame", q=0)
            .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
        )
    except Exception as e:
        raise RuntimeError(f"提取音频失败: {e}") from e

    return audio_path
