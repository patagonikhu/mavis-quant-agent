"""LLM 客户端

基于 LiteLLM 封装 Qwen (DashScope) 和 DeepSeek 调用。
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_litellm import ChatLiteLLM

from app.config import get_settings

logger = logging.getLogger(__name__)

_llm: Optional[ChatLiteLLM] = None


def create_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> ChatLiteLLM:
    """创建 LLM 客户端

    Args:
        provider: 提供者 ("qwen" / "deepseek"), 留空读配置
        model: 模型名称, 留空自动选择
        api_key: API Key, 留空读配置
        api_base: API Base URL, 留空自动选择
        temperature: 温度参数
        max_tokens: 最大输出 token 数
    """
    settings = get_settings()

    _provider = provider or settings.llm_provider
    _model = model or settings.get_litellm_model()
    _api_key = api_key or settings.llm_api_key
    _api_base = api_base or settings.get_llm_api_base()

    if not _api_key:
        raise ValueError(
            f"未配置 LLM API Key。请在 .env 中设置 LLM_API_KEY\n"
            f"  当前提供者: {_provider}\n"
            f"  当前模型: {_model}\n"
            f"  API Base: {_api_base}"
        )

    logger.info("初始化 LLM: provider=%s, model=%s", _provider, _model)

    return ChatLiteLLM(
        model=_model,
        api_key=_api_key,
        api_base=_api_base,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def get_llm() -> ChatLiteLLM:
    """获取全局 LLM 单例"""
    global _llm
    if _llm is None:
        _llm = create_llm()
    return _llm


def reset_llm() -> None:
    """重置 LLM 单例 (用于测试或切换模型)"""
    global _llm
    _llm = None
