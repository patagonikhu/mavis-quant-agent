"""
factors/utils.py — factor 共用 helper

提供:
  - df_from_bars(): KLineBar 列表 / dict-of-list / DataFrame 统一转 DataFrame
  - asof_slice(): 按 trade_date 列切到 asof 当天及之前 (支持多种日期格式)
  - clamp(): 数值裁剪到 [lo, hi]
  - _amount_proxy(): amount 列缺失/全 0 时用 volume*close 兜底 (单位对齐到千元)

无网络请求,纯本地计算。
"""
from __future__ import annotations
from typing import Iterable, Sequence
import pandas as pd


def df_from_bars(
    bars: Iterable | pd.DataFrame,
    include: Sequence[str] = (
        "close", "high", "low", "open", "volume", "amount", "pct_chg", "trade_date",
    ),
) -> pd.DataFrame:
    """
    把 KLineBar 列表 / dict-of-list / DataFrame 统一转 DataFrame。

    - bars 是 DataFrame 时返回 copy
    - bars 是 KLineBar 列表时取每个对象的对应属性
    - bars 是 dict-of-list 时按 key 拉取

    Args:
        bars: KLineBar 列表 / DataFrame / dict
        include: 需要的列名

    Returns:
        pd.DataFrame, 列名按 include 顺序
    """
    if isinstance(bars, pd.DataFrame):
        out = bars.copy()
        for col in include:
            if col not in out.columns:
                out[col] = pd.NA
        return out[list(include)]

    rows = []
    for bar in bars:
        if isinstance(bar, dict):
            # 已经是 dict,只取需要的列
            rows.append({col: bar.get(col) for col in include})
        elif isinstance(bar, pd.DataFrame):
            # 不应该到这里,但防御一下
            return bar.copy()
        else:
            # KLineBar dataclass / pydantic / 普通对象
            rows.append({col: getattr(bar, col, None) for col in include})

    if not rows:
        return pd.DataFrame(columns=list(include))

    df = pd.DataFrame(rows)
    # 确保 include 里所有列都存在(可能是 dict 缺字段)
    for col in include:
        if col not in df.columns:
            df[col] = pd.NA
    return df[list(include)]


def normalize_asof(asof: str | None) -> str | None:
    """统一 asof 格式为 YYYYMMDD, None 透传。"""
    if asof is None:
        return None
    s = str(asof).strip().replace("-", "").replace("/", "")[:8]
    return s if len(s) == 8 else None


def asof_slice(df: pd.DataFrame | None, asof: str | None) -> pd.DataFrame | None:
    """
    把 K 线 DataFrame 切到 asof 当天 (含) 之前的所有行。

    Args:
        df: K 线 DataFrame, 需含 trade_date 列 (YYYYMMDD) 或 date 列 (datetime)
        asof: 'YYYY-MM-DD' / 'YYYYMMDD' / None (None 透传 df)

    Returns:
        切片后的 DataFrame, 若 df 为空返回原值
    """
    if df is None or df.empty or asof is None:
        return df

    asof_norm = normalize_asof(asof)
    if asof_norm is None:
        return df

    if "trade_date" in df.columns:
        td = df["trade_date"].astype(str).str.replace("-", "").str.replace("/", "").str[:8]
        return df[td <= asof_norm].copy()
    if "date" in df.columns:
        d = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y%m%d")
        return df[d <= asof_norm].copy()
    return df


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """数值裁剪到 [lo, hi]"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def _amount_proxy(df: pd.DataFrame) -> pd.Series:
    """
    amount 列缺失或全 0 时,用 volume*close 兜底并换算到"千元"单位。

    老 data 工具 里 amount 字段单位是"千元"(tushare_fetcher 输出),所以兜底时:
      amount_千元 = volume × close / 1000

    Args:
        df: 含 volume / close 列的 DataFrame

    Returns:
        pd.Series (单位:千元)
    """
    if "amount" in df.columns:
        amt = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
        if float(amt.tail(20).sum()) > 0:
            return amt
    vol = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0.0)
    close = pd.to_numeric(df.get("close"), errors="coerce").fillna(0.0)
    return vol * close / 1000.0