"""配置读取：从项目根目录 .env 加载豆包 API 密钥等配置"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（dy_extractor/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 加载 .env（若存在）
load_dotenv(PROJECT_ROOT / ".env")


def get_api_key() -> str:
    """返回豆包语音 API Key，未配置则抛错"""
    key = os.getenv("DOUBAO_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "未配置 DOUBAO_API_KEY。请把 .env.example 复制为 .env 并填入密钥，"
            "或设置环境变量 DOUBAO_API_KEY。"
        )
    return key


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
