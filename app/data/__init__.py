"""数据层入口

用法:
    from app.data import get_data_provider
    provider = get_data_provider()
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from app.data.provider import DataProvider


@lru_cache(maxsize=1)
def get_data_provider() -> DataProvider:
    """获取数据提供者单例（由 data_provider 配置决定）"""
    from app.config import get_settings
    settings = get_settings()

    from app.data.tushare_provider import TushareProvider
    return TushareProvider(token=settings.tushare_token)