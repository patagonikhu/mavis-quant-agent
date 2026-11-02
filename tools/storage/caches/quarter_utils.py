"""
tools/storage/caches/quarter_utils.py — 季度工具 (纯函数)

2026-10-28 新建: fflow 完全删除时, 原 `caches/fflow_history.py` 里的通用季度函数
被大量复用 (backtest_blowout / blowout_backtest_v630 /
finance_earnings_blowout / sync.py), 跟着一起删会连带 5 处 ImportError.
所以把 3 个跟 fflow 无关的纯函数搬到这里, 保持原语义不变。

公开 API:
  - _quarter_of(date_str)                → "YYYYQN"
  - quarters_since(start_date, end_date) → ["2024Q1", ...]
  - latest_disclosed_quarter(dir)        → "YYYYQN" | None

⚠️ 铁律 (2026-10-14 固化): 业务运行时永远不要硬编码 "2026Q2" / "20250630" 这类
   季度/日期字符串, 用这里的工具函数动态算.

**区别 `_quarter_of(today)` vs `latest_disclosed_quarter()`**:
  - 10/14 时 today=2026Q4 但 Q3 财报 10/15 才披露, 所以 latest_disclosed=2026Q3
  - 用在: 业绩过滤 / 回测 end 边界 (不要用 _quarter_of(today) 拿当季)
"""
from __future__ import annotations

from pathlib import Path


def _quarter_of(date_str: str) -> str:
    """YYYYMMDD → YYYYQN (1-3=Q1, 4-6=Q2, 7-9=Q3, 10-12=Q4)

    Args:
        date_str: "20260823" 这样的 8 位日期字符串
    Returns:
        "2026Q3"
    """
    y, m = int(date_str[:4]), int(date_str[4:6])
    q = (m - 1) // 3 + 1
    return f"{y}Q{q}"


def quarters_since(start_date: str, end_date: str) -> list[str]:
    """[start_date, end_date] 区间内所有季度标识 (含两端)

    Args:
        start_date: YYYYMMDD
        end_date:   YYYYMMDD
    Returns:
        ["2025Q3", "2025Q4", "2026Q1", ...] 升序
    """
    y1, m1 = int(start_date[:4]), int(start_date[4:6])
    y2, m2 = int(end_date[:4]), int(end_date[4:6])
    q1 = (m1 - 1) // 3 + 1
    q2 = (m2 - 1) // 3 + 1
    out = []
    y, q = y1, q1
    while (y, q) <= (y2, q2):
        out.append(f"{y}Q{q}")
        q += 1
        if q > 4:
            q = 1
            y += 1
    return out


def latest_disclosed_quarter(parquet_dir: str = "data/history/financials") -> str | None:
    """已落盘的最���财务季 (YYYYQN)

    读 financials 目录下的 parquet 文件名 (形如 2026Q2.parquet), 取最大的一个。
    比 _quarter_of(today) 准: 10/14 时今天属 2026Q4 但 Q3 财报 10/15 才披露。

    Args:
        parquet_dir: financials parquet 所在目录
    Returns:
        "2026Q2" | None (目录不存在或无 parquet)
    """
    d = Path(parquet_dir)
    if not d.exists():
        return None
    quarters = [p.stem for p in d.glob("*.parquet") if p.stem[:4].isdigit() and len(p.stem) == 6]
    if not quarters:
        return None
    return max(quarters)
