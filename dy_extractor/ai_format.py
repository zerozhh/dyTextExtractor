"""AI 文案排版：调用 DeepSeek V4 Flash 对口播文案做排版整理。

架构说明：
- 本模块是 LLM 适配层，只做「文本进 → 排版文本出」，与 HTTP/WebUI 无关。
  可独立测试，命令行等其他入口也能复用；换模型只需改 .env。
- 排版契约（见 _FORMAT_PROMPT）：只整理排版，不增删内容、不概括、不改写原意。
  它是「整理器」不是「改写器」。
"""

import requests

from .config import (
    get_deepseek_api_key,
    get_deepseek_base_url,
    get_deepseek_model,
)

API_PATH = "/chat/completions"

# 排版提示词：明确「只排版不改写」的契约
_FORMAT_PROMPT = """你是一个严谨的中文文案排版助手。用户会给你一段由语音识别得到的中文口播文案（可能没有分段、标点不全、含有口语填充词）。

请对它进行【排版整理】，严格遵守以下要求：
1. 只做排版和文字润色，不得增删内容、不得概括、不得改变原意、不得改写任何事实与数字
2. 按语义自然分段，一个话题一个段落
3. 修正明显的标点错误，补充必要的句号、逗号
4. 删除口语中无意义的填充词（如 嗯、啊、呃、那个、就是说 等）
5. 保留原有的专有名词、数字、口语化表达

直接输出整理后的完整文本，不要输出任何解释、前缀、标题或引号。"""


class LLMError(RuntimeError):
    """LLM 调用错误，带错误码供前端展示。"""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def _friendly_error(resp: requests.Response) -> str:
    """把 DeepSeek 的 HTTP 错误转成友好中文提示。"""
    code = resp.status_code
    if code == 401:
        return "DEEPSEEK_API_KEY 无效，请检查 .env 中的密钥"
    if code == 402:
        return "DeepSeek 余额不足，请充值后重试"
    if code == 429:
        return "DeepSeek 请求过于频繁或并发受限，请稍后重试"
    if code == 404:
        return f"模型 {get_deepseek_model()} 不存在，请检查 .env 中的 DEEPSEEK_MODEL"
    try:
        msg = resp.json().get("error", {}).get("message", "")
    except Exception:
        msg = ""
    return f"AI 调用失败 (HTTP {code})：{msg or resp.text[:200]}"


def _call_deepseek(messages: list[dict], temperature: float = 0.3) -> str:
    """调用 DeepSeek 对话补全接口（OpenAI 兼容），返回 assistant 的文本。"""
    api_key = get_deepseek_api_key()
    base_url = get_deepseek_base_url().rstrip("/")
    model = get_deepseek_model()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }

    try:
        resp = requests.post(f"{base_url}{API_PATH}", headers=headers, json=payload, timeout=120)
    except requests.exceptions.RequestException as e:
        raise LLMError(f"网络请求失败：{e}", code="network") from e

    if resp.status_code != 200:
        raise LLMError(_friendly_error(resp), code=str(resp.status_code))

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise LLMError(f"AI 返回异常：{resp.text[:300]}") from None

    return (content or "").strip()


def format_transcript(text: str) -> str:
    """对一段口播文案做 AI 排版整理，返回排版后的文本。"""
    if not text or not text.strip():
        raise LLMError("文案为空，无法排版")

    messages = [
        {"role": "system", "content": "你是一个严谨的中文文案排版助手，只做排版，不改变内容。"},
        {"role": "user", "content": f"{_FORMAT_PROMPT}\n\n需要排版的文案：\n\"\"\"\n{text}\n\"\"\""},
    ]
    return _call_deepseek(messages)
