"""豆包 Seed-ASR 2.0 语音识别（base64 直传音频，无需上传到对象存储）。

接口（经开源实现验证）：
- 提交: POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit
- 查询: POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/query  (json={})
- 认证: Header X-Api-Key + X-Api-Resource-Id: volc.seedasr.auc +
        X-Api-Request-Id(UUID) + X-Api-Sequence: -1
- 状态: 响应 Header X-Api-Status-Code:
        20000000=识别完成  20000001/20000002=仍在处理
- 结果: 响应 body 的 result.text（聚合全文）+ result.utterances（逐句时间轴，
        单位为毫秒，实测确认）；transcribe() 返回 AsrResult 结构化结果

音频通过 audio.data(base64) 直接随请求体提交，抖音短视频的音频大小完全够用。
"""

import base64
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests

BASE_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"
RESOURCE_ID = "volc.seedasr.auc"
POLL_INTERVAL_SECONDS = 1.0
TIMEOUT_SECONDS = 300.0
UID = "dy-text-extractor"


@dataclass
class Utterance:
    """ASR 识别出的一个带时间戳的句子（start/end 单位：毫秒）。"""

    start_ms: int
    end_ms: int
    text: str


@dataclass
class AsrResult:
    """一次语音识别的完整结果：聚合全文 + 逐句时间轴。

    文案（transcript.md）与字幕（subtitles.srt）都是这份数据的视图。
    这里只生产数据、不做格式化——渲染交给各消费方（subtitles 等）。
    """

    text: str
    utterances: list[Utterance]


class DoubaoASRError(RuntimeError):
    """豆包识别过程中的错误，带错误码用于前端展示。"""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def _headers(api_key: str, request_id: str, resource_id: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": request_id,
        "X-Api-Sequence": "-1",
    }


def _parse_error_code(response: requests.Response) -> str:
    """从响应中提取错误码：优先 Header X-Api-Status-Code，其次 body.header.code。"""
    status = response.headers.get("X-Api-Status-Code", "")
    if status:
        return status
    try:
        data = response.json()
    except ValueError:
        return ""
    header = data.get("header", {}) if isinstance(data, dict) else {}
    return str(header.get("code", ""))


# 常见错误码 → 给用户的中文指引
_ERROR_HINTS = {
    "45000030": (
        "尚未开通「录音文件识别」服务权限。请到火山引擎控制台 → 开通管理 → 语音模型 "
        "→ 开通「Doubao-录音文件识别 2.0」后重试。"
    ),
    "45000010": "App ID 与密钥不匹配，请检查 API Key 配置是否正确。",
    "45000131": "音频时长超过限制，请换一段更短的视频再试。",
    "1001": "请求参数无效，请检查分享链接和音频格式。",
    "1002": "API Key 无效或没有访问权限，请检查 .env 中的 DOUBAO_API_KEY。",
    "1003": "请求过于频繁，请稍等几秒后重试。",
    "network": "网络请求失败，请检查网络连接后重试。",
    "timeout": "识别超时，请稍后重试。",
}


def _friendly_error(code: str, detail: str) -> str:
    """把错误码翻译成友好提示，未知错误码保留原始信息。"""
    hint = _ERROR_HINTS.get(code)
    if hint:
        return hint
    return f"豆包识别失败（错误码 {code or '未知'}）：{detail}"


def _ensure_ok(response: requests.Response, phase: str) -> None:
    """检查提交/查询是否成功，失败抛出带错误码的友好提示。"""
    code = _parse_error_code(response)
    if response.status_code >= 400 or code != "20000000":
        detail = response.headers.get("X-Api-Message", "") or response.text[:300]
        raise DoubaoASRError(_friendly_error(code, detail), code=code)


def _parse_result(response: requests.Response) -> AsrResult:
    """从查询响应中解析结构化识别结果（聚合文本 + 逐句时间轴）。

    utterances 的单位是毫秒（实测确认）；单条字段异常时跳过该条，
    不拖垮整体。words 词级时间戳当前不消费。
    """
    if not response.content:
        raise DoubaoASRError("识别结果为空")
    data = response.json()

    result = data.get("result")
    if isinstance(result, dict) and result.get("text"):
        text = str(result["text"]).strip()
    elif data.get("text"):
        text = str(data["text"]).strip()
    else:
        raise DoubaoASRError(f"识别结果中未找到文本: {str(data)[:500]}")

    utterances: list[Utterance] = []
    items = result.get("utterances") or [] if isinstance(result, dict) else []
    for item in items:
        try:
            utterances.append(
                Utterance(
                    start_ms=int(item["start_time"]),
                    end_ms=int(item["end_time"]),
                    text=str(item.get("text", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return AsrResult(text=text, utterances=utterances)


def transcribe(
    api_key: str,
    audio_path: Path,
    language: str = "auto",
    resource_id: str = RESOURCE_ID,
) -> AsrResult:
    """识别本地音频文件，返回结构化结果（聚合全文 + 逐句时间轴）。

    language: 语种代码，auto=自动识别，zh-CN=中文，en-US=英文，ja-JP=日文等。
    """
    audio_bytes = audio_path.read_bytes()
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

    request_id = str(uuid.uuid4())
    headers = _headers(api_key, request_id, resource_id)

    submit_body = {
        "user": {"uid": UID},
        "audio": {"format": "mp3", "data": audio_b64, "language": language},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,   # 数字归一化
            "enable_punc": True,  # 自动加标点
            "show_utterances": True,
        },
    }

    # 1. 提交任务
    try:
        resp = requests.post(f"{BASE_URL}/submit", headers=headers, json=submit_body, timeout=60)
    except requests.exceptions.RequestException as e:
        raise DoubaoASRError(f"网络请求失败：{e}", code="network") from e
    _ensure_ok(resp, "提交")

    # 2. 轮询查询结果（网络抖动时自动重试几次）
    deadline = time.monotonic() + TIMEOUT_SECONDS
    consecutive_network_errors = 0
    while time.monotonic() < deadline:
        try:
            resp = requests.post(f"{BASE_URL}/query", headers=headers, json={}, timeout=60)
            consecutive_network_errors = 0
        except requests.exceptions.RequestException as e:
            consecutive_network_errors += 1
            if consecutive_network_errors >= 5:
                raise DoubaoASRError(f"网络请求连续失败：{e}", code="network") from e
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        status = resp.headers.get("X-Api-Status-Code", "")

        if status == "20000000":
            return _parse_result(resp)
        if status in ("20000001", "20000002"):
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        _ensure_ok(resp, "查询")

    raise DoubaoASRError(f"识别超时（超过 {TIMEOUT_SECONDS:.0f}s）", code="timeout")
