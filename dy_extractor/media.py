"""媒体文件完整性校验工具（基于 ffmpeg 流拷贝）。

第一性原理：一个 mp4 的容器时长（moov 元数据）是可以“说谎”的——下载被截断、
或 CDN 返回残缺文件时，头部仍可能声明完整时长，但实际媒体数据（mdat）只覆盖
前一小段。这类文件播放到某处就会卡住，且音频提取、ASR 都会基于残缺内容产出
错误结果。

因此“文件非空”≠“文件完整”。这里用 ffmpeg 流拷贝（-c copy，只拆包不解码，
与 ffprobe 逐包扫描语义等价且同为 IO 级开销）读完整个文件，取处理进度到达的
最后时刻，与容器声明时长对比：残缺文件的进度终点会明显小于声明时长。

为什么不用 ffprobe：容器镜像的 ffmpeg 来自 imageio-ffmpeg 静态包，只含
ffmpeg 二进制、不含 ffprobe（2026-08 Docker 部署踩坑：校验把「工具缺席」当
「文件不完整」，误删完好视频）。统一只依赖 ffmpeg，宿主（brew）与容器
（imageio-ffmpeg）行为一致。

使用场景：下载完成后校验、复用缓存前校验。校验失败的文件绝不可落盘为合法缓存。
"""

import re
import subprocess
from pathlib import Path

# 容差（秒）：允许容器时长与真实数据尾部的微小偏差（如尾帧/标签差异）。
# 远小于一个“缺了大段内容”的残缺文件的缺口。
_TOLERANCE = 2.0

_FFMPEG_TIMEOUT = 120  # 秒；流拷贝只受 IO 限制，长视频也应在该时间内完成

# ffmpeg 的时长行与状态行解析。状态行以 \r 就地刷新（非 tty 下也输出，
# -stats 强制开启），不能按行 split，只能全文正则。
_DURATION_RE = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _to_seconds(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def media_is_complete(path: Path, tolerance: float = _TOLERANCE) -> bool:
    """校验媒体文件是否完整：真实媒体数据须覆盖到接近容器声明的时长。

    返回 False 的情况：文件不存在、校验超时、容器时长缺失/不可解析、
    或流拷贝到达的最后时刻 << 容器时长（即文件被截断）。

    抛 RuntimeError：ffmpeg 不可用。「无法校验」≠「文件不完整」——静默按
    不完整处理会误删完好文件、误导用户重试，并让缓存层把不可信当可信的反面
    处理，必须大声失败。
    """
    if not path.is_file():
        return False
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-v", "info", "-nostdin", "-stats",
                "-i", str(path),
                "-c", "copy", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=_FFMPEG_TIMEOUT,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "ffmpeg 不可用，无法校验媒体完整性（宿主需 brew install ffmpeg；Docker 镜像需含 ffmpeg）"
        ) from e
    except subprocess.TimeoutExpired:
        return False

    err = proc.stderr or ""
    dur = _DURATION_RE.search(err)
    if not dur:
        return False
    fmt_duration = _to_seconds(*dur.groups())
    if fmt_duration <= 0:
        return False

    times = _TIME_RE.findall(err)
    if not times:
        return False
    last_end = max(_to_seconds(*t) for t in times)
    return last_end >= fmt_duration - tolerance
