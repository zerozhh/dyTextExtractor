"""配置读取：从项目根目录 .env 加载豆包 API 密钥等配置"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（dy_extractor/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 加载 .env（若存在）
load_dotenv(PROJECT_ROOT / ".env")


def get_api_key() -> str:
    """返回豆包语音 API Key（新版控制台单头鉴权 X-Api-Key）。

    未配置 API Key 时：若已配齐 appid+token 双头凭据（DOUBAO_APP_ID +
    DOUBAO_ACCESS_TOKEN）则返回空串——双头鉴权已足够；否则抛错。
    """
    key = os.getenv("DOUBAO_API_KEY", "").strip()
    if not key and not (get_app_id() and get_access_token()):
        raise RuntimeError(
            "未配置豆包语音凭据。二选一："
            "① 新版控制台 → 配置 DOUBAO_API_KEY（单头鉴权）；"
            "② 旧版控制台 → 配置 DOUBAO_APP_ID + DOUBAO_ACCESS_TOKEN（双头鉴权）。"
        )
    return key


def get_app_id() -> str:
    """返回豆包语音 APP ID（旧版控制台双头鉴权 X-Api-App-Key），未配置返回空串。"""
    return os.getenv("DOUBAO_APP_ID", "").strip()


def get_access_token() -> str:
    """返回豆包语音 Access Token（旧版控制台双头鉴权 X-Api-Access-Key），未配置返回空串。"""
    return os.getenv("DOUBAO_ACCESS_TOKEN", "").strip()


def get_language() -> str:
    """返回识别语言，默认 auto（自动识别语种，中英混合也能正确适配）"""
    return os.getenv("DOUBAO_LANGUAGE", "auto").strip() or "auto"


def get_resource_id() -> str:
    """返回豆包语音资源 ID。

    默认 Seed-ASR 2.0 标准版：volc.seedasr.auc
    若开通的是极速版，可改为：volc.bigasr.auc_turbo
    """
    return os.getenv("DOUBAO_RESOURCE_ID", "volc.seedasr.auc").strip() or "volc.seedasr.auc"


# ===== DeepSeek（AI 文案排版）=====

DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"


def deepseek_configured() -> bool:
    """是否已配置 DeepSeek API Key（供前端提示）。"""
    return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


def get_deepseek_api_key() -> str:
    """返回 DeepSeek API Key，未配置则抛错。"""
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "未配置 DEEPSEEK_API_KEY。请在 .env 中添加 DEEPSEEK_API_KEY=sk-xxx"
        )
    return key


def get_deepseek_model() -> str:
    """返回 DeepSeek 模型，默认 deepseek-v4-flash。"""
    return os.getenv("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL).strip() or DEEPSEEK_DEFAULT_MODEL


def get_deepseek_base_url() -> str:
    """返回 DeepSeek API base url，默认 https://api.deepseek.com。"""
    return os.getenv("DEEPSEEK_BASE_URL", DEEPSEEK_DEFAULT_BASE_URL).strip() or DEEPSEEK_DEFAULT_BASE_URL
