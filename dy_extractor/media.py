"""媒体文件完整性校验工具（基于 ffprobe）。

第一性原理：一个 mp4 的容器时长（moov 元数据）是可以“说谎”的——下载被截断、
或 CDN 返回残缺文件时，头部仍可能声明完整时长，但实际媒体数据（mdat）只覆盖
前一小段。这类文件播放到某处就会卡住，且音频提取、ASR 都会基于残缺内容产出
错误结果。

因此“文件非空”≠“文件完整”。这里用 ffprobe 逐包读取真实媒体数据，取最大包
结束时间，与容器声明时长对比：残缺文件的最大包时间戳会明显小于声明时长。

使用场景：下载完成后校验、复用缓存前校验。校验失败的文件绝不可落盘为合法缓存。
"""

import json
import subprocess
from pathlib import Path

# 容差（秒）：允许容器时长与真实数据尾部的微小偏差（如尾帧/标签差异）。
# 远小于一个“缺了大段内容”的残缺文件的缺口（本例缺口达 520 秒）。
_TOLERANCE = 2.0

_FFPROBE_TIMEOUT = 120  # 秒；长视频逐包扫描也应在该时间内完成


def media_is_complete(path: Path, tolerance: float = _TOLERANCE) -> bool:
    """校验媒体文件是否完整：真实媒体数据须覆盖到接近容器声明的时长。

    返回 False 的情况：文件不存在、ffprobe 不可用/超时、容器时长缺失、
    或逐包读取后最大包结束时间 << 容器时长（即文件被截断）。
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration:packet=pts_time,duration_time",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False

    if proc.returncode != 0:
        return False

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False

    fmt_duration = float(data.get("format", {}).get("duration") or 0)
    if fmt_duration <= 0:
        return False

    last_end = max(
        (
            float(pkt.get("pts_time") or 0) + float(pkt.get("duration_time") or 0)
            for pkt in data.get("packets", [])
        ),
        default=0.0,
    )
    return last_end >= fmt_duration - tolerance
