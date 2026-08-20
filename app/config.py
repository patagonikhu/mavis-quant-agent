"""应用配置管理

使用 pydantic-settings 从环境变量 / .env 文件加载配置。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 忽略 .env 中未声明的字段（如 MYSQL_* 属于 app.sync，不归 main 关心）
    )

    # ---- LLM ----
    llm_provider: Literal["qwen", "deepseek"] = Field(
        default="qwen",
        description="LLM 提供者",
    )
    llm_model: str = Field(
        default="",
        description="LLM 模型名称, 留空则根据 provider 自动选择",
    )
    llm_api_key: str = Field(
        default="",
        description="LLM API Key",
    )
    llm_api_base: str = Field(
        default="",
        description="LLM API Base URL, 留空使用默认",
    )

    # ---- 数据源 ----
    data_provider: Literal["akshare", "tushare", "auto"] = Field(
        default="akshare",
        description="数据提供者",
    )
    tushare_token: str = Field(
        default="",
        description="Tushare API Token",
    )

    # ---- 数据库 ----
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/quant.db",
        description="数据库连接串",
    )

    # ---- 服务器 ----
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # ---- 调试 ----
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # ---- 调度 ----
    scheduler_enabled: bool = Field(
        default=False,
        description="是否启动盘中自动扫描调度器",
    )

    # ---- 告警 ----
    alert_webhook_url: str = Field(
        default="",
        description="告警 Webhook URL（钉钉/飞书/通用）",
    )
    alert_webhook_type: Literal["dingtalk", "feishu", "generic"] = Field(
        default="generic",
        description="Webhook 类型",
    )
    alert_webhook_secret: str = Field(
        default="",
        description="钉钉机器人签名密钥（可选）",
    )
    alert_min_score: float = Field(
        default=50.0,
        description="触发告警的最低评分阈值",
    )

    # ---- 默认模型映射 ----
    _DEFAULT_MODELS: dict[str, str] = {
        "qwen": "qwen-plus",
        "deepseek": "deepseek-chat",
    }

    _DEFAULT_API_BASES: dict[str, str] = {
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "deepseek": "https://api.deepseek.com",
    }

    def get_llm_model(self) -> str:
        """获取实际使用的模型名称"""
        if self.llm_model:
            return self.llm_model
        return self._DEFAULT_MODELS.get(self.llm_provider, "qwen-plus")

    def get_llm_api_base(self) -> str:
        """获取实际使用的 API Base URL"""
        if self.llm_api_base:
            return self.llm_api_base
        return self._DEFAULT_API_BASES.get(
            self.llm_provider,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def get_litellm_model(self) -> str:
        """获取 LiteLLM 格式的模型名称

        Qwen 通过 OpenAI 兼容接口调用, 前缀 openai/
        DeepSeek 直接通过 litellm 的 deepseek/ 前缀调用
        """
        model = self.get_llm_model()
        if self.llm_provider == "qwen":
            return f"openai/{model}"
        if self.llm_provider == "deepseek":
            return f"deepseek/{model}" if not model.startswith("deepseek/") else model
        return model


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
