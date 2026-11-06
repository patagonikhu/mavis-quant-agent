"""内存 LRU 缓存

轻量级异步缓存，无需 Redis 依赖，适合单机开发。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional


class _Entry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl


class DataCache:
    """异步内存缓存（带 TTL + LRU 淘汰）"""

    # 各数据类别的缓存时长 (秒)
    TTL_REALTIME = 60        # 实时行情 — 1 分钟
    TTL_KLINE = 3600         # K 线 — 1 小时 (跟 tushare 侧内存缓存同量级)
    TTL_FINANCIAL = 86400    # 财务 — 1 天
    TTL_INDEX = 3600         # 指数 — 1 小时
    TTL_SEARCH = 3600        # 搜索结果 — 1 小时

    def __init__(self, max_size: int = 1000):
        self._store: dict[str, _Entry] = {}
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                return None
            return entry.value

    async def set(self, key: str, value: Any, ttl: float = 300) -> None:
        async with self._lock:
            self._evict_expired()
            if len(self._store) >= self._max_size:
                # 删除最早插入的 10%
                to_remove = max(1, self._max_size // 10)
                for k in list(self._store.keys())[:to_remove]:
                    del self._store[k]
            self._store[key] = _Entry(value, ttl)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, e in self._store.items() if now > e.expires_at]
        for k in expired:
            del self._store[k]


def cache_key_quote(symbol: str) -> str:
    return f"quote:{symbol}"


def cache_key_kline(symbol: str, period: str, count: int) -> str:
    return f"kline:{symbol}:{period}:{count}"


def cache_key_financial(symbol: str) -> str:
    return f"financial:{symbol}"


def cache_key_index(symbol: str) -> str:
    return f"index:{symbol}"


def cache_key_search(keyword: str) -> str:
    return f"search:{keyword}"


def cache_key_sector_kline(sector_name: str, count: int) -> str:
    return f"sector_kline:{sector_name}:{count}"


def cache_key_sector_constituents(sector_name: str) -> str:
    return f"sector_cons:{sector_name}"


# 模块级单例 (provider 与 tests 共用同一个缓存实例)
cache = DataCache()