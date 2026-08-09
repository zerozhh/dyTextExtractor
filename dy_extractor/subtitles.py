"""字幕格式化层：把 ASR 的逐句时间轴渲染成 SRT 字幕文本。

第一性原理：数据贵、格式便宜。一次识别返回的逐句时间轴是唯一的昂贵数据，
SRT 只是它的一种序列化。本模块是纯函数格式化层——无 IO、无网络、无状态，
可独立单测；未来要 VTT / JSON 等格式，只需在这里加一个渲染函数。

SRT 时间轴约定：HH:MM:SS,mmm（毫秒三位）。
"""

from typing import List

from .doubao_asr import Utterance


def _fmt_timestamp(ms: int) -> str:
    """毫秒 → SRT 时间戳：HH:MM:SS,mmm。"""
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def to_srt(utterances: List[Utterance]) -> str:
    """把逐句时间轴渲染成 SRT 文本。

    格式：序号 / 开始 --> 结束 / 文本，段间空行。空列表返回空串。
    """
    if not utterances:
        return ""
    blocks = []
    for i, u in enumerate(utterances, start=1):
        blocks.append(
            f"{i}\n{_fmt_timestamp(u.start_ms)} --> {_fmt_timestamp(u.end_ms)}\n{u.text}"
        )
    return "\n\n".join(blocks) + "\n"
