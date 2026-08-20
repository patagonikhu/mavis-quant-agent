"""结构化输出 + 降级解析

当 LLM 无法正确生成 JSON 时提供手动解析降级。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, Type, TypeVar

from langchain_core.messages import AIMessage
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def parse_structured_output(
    response: AIMessage | str,
    model_class: Type[T],
) -> Optional[T]:
    """从 LLM 响应中解析 Pydantic 模型

    尝试顺序:
    1. 直接 JSON 解析
    2. 从 markdown 代码块中提取 JSON
    3. 从文本中查找 JSON 对象

    Args:
        response: LLM 响应 (AIMessage 或字符串)
        model_class: 目标 Pydantic 模型类
    """
    text = response.content if isinstance(response, AIMessage) else str(response)

    if not text.strip():
        return None

    # 尝试 1: 直接解析
    try:
        data = json.loads(text)
        return model_class.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        pass

    # 尝试 2: 从 markdown 代码块提取
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if code_block:
        try:
            data = json.loads(code_block.group(1).strip())
            return model_class.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

    # 尝试 3: 查找 JSON 对象
    json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            return model_class.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

    logger.warning("无法从 LLM 响应中解析 %s 结构", model_class.__name__)
    return None


def extract_json_from_text(text: str) -> Optional[dict[str, Any]]:
    """从文本中提取 JSON 对象 (宽松模式)"""
    if not text:
        return None

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 查找最外层的 JSON 对象
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None
