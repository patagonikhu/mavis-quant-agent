"""
tools/btf/framework.py — 回测框架核心 (4 个抽象类)

设计:
  DataLayer      - 读 analysis_cache, 准备 (code, date, close, signal) 表
  Strategy       - 选股: 给定日期, 选 top N 票
  Portfolio      - 持仓: 等权 / 加权, 持仓 N 天
  Evaluator      - 评估: 1 维度 1 类 (收益/风险/风险调整/交易)

4 抽象都是 Strategy pattern, 跑回测时自由组合.

用法:
    from tools.btf import (
        BacktestRunner, AnalysisCacheData,
        EY8Strategy, ROC25Strategy, ROC25EY8Strategy, PEG15Strategy, MarketStrategy,
        EqualWeightPortfolio,
        ReturnEvaluator, RiskEvaluator, RiskAdjReturnEvaluator, TradingEvaluator,
    )
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import sqlite3
from pathlib import Path


_PROJECT = Path(__file__).resolve().parent.parent.parent


# ============================================================
# Trade 数据结构
# ============================================================

@dataclass
class Trade:
    """单笔交易记录"""
    code: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float    # (exit-entry)/entry
    hold_days: int
    exit_reason: str = "hold_days"   # "hold_days" / "stop_loss" / "manual"


@dataclass
class NavPoint:
    """单日净值"""
    date: str
    nav: float           # 归一化 (1.0 起)
    cash: float = 0.0
    holdings_value: float = 0.0
    n_holdings: int = 0


# ============================================================
# 1) DataLayer — 读 analysis_cache
# ============================================================

class DataLayer(ABC):
    """数据源抽象"""

    @abstractmethod
    def load_window(self, start: str, end: str, codes: Optional[list[str]] = None) -> pd.DataFrame:
        """加载回测窗口

        Returns:
            DataFrame[code, date, close, roc, ey, peg, dcf_l, daily_return]
            daily_return 算好 (close.pct_change)
        """
        pass


class AnalysisCacheData(DataLayer):
    """从 analysis_cache.db 读估值列 + 从 daily K线 读 close (合并)

    analysis_cache 表不含 close (只存指标), 所以要 join daily K 线.
    1 年窗口 (248 天 × 1374 票 ≈ 34 万行) 2 次 SQL.
    """

    def __init__(self, db_path: str = "data/analysis_cache.db", kline_dir: str = "data/history/daily"):
        self.db = sqlite3.connect(db_path)
        self.kline_dir = kline_dir

    def load_window(self, start: str, end: str, codes: Optional[list[str]] = None) -> pd.DataFrame:
        # 1) 1 次 SQL 查 valuation 4 列 (analysis_cache)
        if codes:
            codes_str = ",".join(f"'{c}'" for c in codes)
            where_extra = f" AND code IN ({codes_str})"
        else:
            where_extra = ""
        sql = f"""
            SELECT code, date_str, roc, ey, peg, dcf_l
            FROM analysis_cache
            WHERE date_str >= '{start}' AND date_str <= '{end}'{where_extra}
            ORDER BY code, date_str
        """
        df_val = pd.read_sql(sql, self.db)
        if df_val.empty:
            return df_val
        df_val = df_val.rename(columns={"date_str": "date"})

        # 2) 拿 close (v6.1.1 改: 走 DataStore.load_all_kline, 不直接 duckdb)
        from tools.storage.store import DataStore, _to_ts_code
        all_kl = DataStore.load_all_kline(years=10)  # 取多, 后面 filter start/end
        if not all_kl:
            return df_val
        if codes:
            codes_ts = {_to_ts_code(c) for c in codes}
        else:
            codes_ts = set(all_kl.keys())
        rows = []
        for ts, bars in all_kl.items():
            if ts not in codes_ts:
                continue
            for b in bars:
                td = str(b.get("trade_date", "")).replace("-", "")[:8]
                if start <= td <= end:
                    rows.append({"ts_code": ts, "trade_date": td, "close": b.get("close")})
        df_kl = pd.DataFrame(rows)
        if df_kl.empty:
            return df_val
        df_kl["code"] = df_kl["ts_code"].str[:6]
        df_kl["date"] = df_kl["trade_date"].str.replace("-", "").str[:8]
        df_kl = df_kl[["code", "date", "close"]]

        # 3) left join (估值优先, 没估值的票也保留 close 用于回测)
        df = df_kl.merge(df_val, on=["code", "date"], how="left")
        # 4) 算 daily_return (按 code shift 1)
        df = df.sort_values(["code", "date"]).reset_index(drop=True)
        df["daily_return"] = df.groupby("code")["close"].pct_change()
        return df

    def close(self):
        self.db.close()


# ============================================================
# 2) Strategy — 选股
# ============================================================

class Strategy(ABC):
    """选股策略: 给定日期, 选 top N 票"""
    name: str = ""

    @abstractmethod
    def select(self, date: str, today_df: pd.DataFrame, top_n: int = 20) -> list[str]:
        """从 today_df (当天全票快照) 选 top N code

        today_df 含: code, close, roc, ey, peg, dcf_l
        """
        pass


class EY8Strategy(Strategy):
    """Magic 便宜: 选 EY > 8% 的票"""
    name = "EY>8% (Magic 便宜)"

    def select(self, date, today_df, top_n=20):
        sub = today_df[today_df["ey"].notna() & (today_df["ey"] > 8)]
        return sub.sort_values("ey", ascending=False)["code"].head(top_n).tolist()


class ROC25Strategy(Strategy):
    """Magic 好公司: 选 ROC > 25% 的票"""
    name = "ROC>25% (Magic 好公司)"

    def select(self, date, today_df, top_n=20):
        sub = today_df[today_df["roc"].notna() & (today_df["roc"] > 25)]
        return sub.sort_values("roc", ascending=False)["code"].head(top_n).tolist()


class ROC25EY8Strategy(Strategy):
    """Magic 双优: ROC>25% + EY>8% (核心策略)"""
    name = "ROC>25% + EY>8% (双优)"

    def select(self, date, today_df, top_n=20):
        sub = today_df[
            today_df["roc"].notna() & today_df["ey"].notna()
            & (today_df["roc"] > 25) & (today_df["ey"] > 8)
        ]
        return sub.sort_values(["roc", "ey"], ascending=False)["code"].head(top_n).tolist()


class PEG15Strategy(Strategy):
    """PEG 便宜: 选 PEG < 1.5 的票"""
    name = "PEG<1.5 (便宜)"

    def select(self, date, today_df, top_n=20):
        sub = today_df[today_df["peg"].notna() & (today_df["peg"] < 1.5) & (today_df["peg"] > 0)]
        return sub.sort_values("peg")["code"].head(top_n).tolist()


class MarketStrategy(Strategy):
    """全市场 (基准) — 随机 N 只 (避免字母排序偏向小盘/退市)"""
    name = "全市场 (基准)"

    def __init__(self, seed: int = 42):
        import random
        self._rng = random.Random(seed)

    def select(self, date, today_df, top_n=20):
        sub = today_df[today_df["close"].notna() & (today_df["close"] > 0)]
        codes = sub["code"].tolist()
        return self._rng.sample(codes, min(top_n, len(codes)))


# ============================================================
# 3) Portfolio — 持仓
# ============================================================

class Portfolio(ABC):
    """持仓模型"""
    def __init__(self, hold_days: int = 20, rebalance: bool = True):
        """rebalance=False → 选 1 次持 248 天 (真实长持对比)
        rebalance=True  → 每天调仓 (默认, 但容易过度乐观)
        """
        self.hold_days = hold_days
        self.rebalance = rebalance
        self.cash: float = 1.0
        self.holdings: dict[str, dict] = {}  # {code: {entry_date, entry_price, shares}}
        self.nav_history: list[NavPoint] = []
        self.trades: list[Trade] = []
        self.open_trade: dict[str, Trade] = {}  # {code: Trade 还在持仓中}

    @abstractmethod
    def on_signal(self, date: str, codes: list[str], prices: dict[str, float]):
        """调仓日: 卖旧 + 买新 (抽象: 资金分配策略由子类决定)"""
        pass

    def on_eod(self, date: str, prices: dict[str, float]):
        """每个交易日: 更新 NAV, 卖到期"""
        # 卖到期的 (rebalance 模式 才卖)
        if self.rebalance:
            for code in list(self.holdings.keys()):
                entry_date = self.holdings[code]["entry_date"]
                days_held = self._days_held(date, entry_date)
                if days_held >= self.hold_days:
                    exit_price = prices.get(code, self.holdings[code]["entry_price"])
                    pnl_pct = (exit_price - self.holdings[code]["entry_price"]) / self.holdings[code]["entry_price"]
                    trade = self.open_trade.pop(code, None)
                    if trade is not None:
                        trade.exit_date = date
                        trade.exit_price = exit_price
                        trade.pnl_pct = pnl_pct
                        trade.hold_days = days_held
                        trade.exit_reason = "hold_days"
                        self.trades.append(trade)
                    else:
                        self.trades.append(Trade(
                            code=code, entry_date=entry_date,
                            entry_price=self.holdings[code]["entry_price"],
                            exit_date=date, exit_price=exit_price,
                            pnl_pct=pnl_pct, hold_days=days_held, exit_reason="hold_days",
                        ))
                    self.cash += self.holdings[code]["shares"] * exit_price
                    del self.holdings[code]
        # 算 NAV
        holdings_value = sum(
            h["shares"] * prices.get(c, h["entry_price"])
            for c, h in self.holdings.items()
        )
        nav = self.cash + holdings_value
        self.nav_history.append(NavPoint(
            date=date, nav=nav, cash=self.cash,
            holdings_value=holdings_value, n_holdings=len(self.holdings),
        ))

    @staticmethod
    def _days_held(today: str, entry_date: str) -> int:
        from datetime import datetime
        try:
            d1 = datetime.strptime(today, "%Y%m%d")
            d2 = datetime.strptime(entry_date, "%Y%m%d")
            return (d1 - d2).days
        except ValueError:
            return 0


class EqualWeightPortfolio(Portfolio):
    """等权: 资金均分 N 只"""

    def on_signal(self, date, codes, prices):
        # 1) 卖所有持仓 (建 trade)
        for code in list(self.holdings.keys()):
            entry = self.holdings[code]
            exit_price = prices.get(code, entry["entry_price"])
            pnl_pct = (exit_price - entry["entry_price"]) / entry["entry_price"] if entry["entry_price"] > 0 else 0
            days_held = self._days_held(date, entry["entry_date"])
            self.cash += entry["shares"] * exit_price
            trade = self.open_trade.pop(code, None)
            if trade is not None:
                trade.exit_date = date
                trade.exit_price = exit_price
                trade.pnl_pct = pnl_pct
                trade.hold_days = days_held
                trade.exit_reason = "rebalance"
            else:
                trade = Trade(
                    code=code, entry_date=entry["entry_date"],
                    entry_price=entry["entry_price"],
                    exit_date=date, exit_price=exit_price,
                    pnl_pct=pnl_pct, hold_days=days_held, exit_reason="rebalance",
                )
            self.trades.append(trade)
        self.holdings.clear()

        # 2) 资金均分, 买新
        if not codes:
            return
        # 用 NAV (cash + 当前 holdings value) — 但 holdings 已 clear, 就是 cash
        per_stock = self.cash / len(codes)
        for code in codes:
            price = prices.get(code)
            if price and price > 0:
                shares = per_stock / price
                self.holdings[code] = {
                    "entry_date": date, "entry_price": price, "shares": shares,
                }
                self.open_trade[code] = Trade(
                    code=code, entry_date=date, entry_price=price,
                    exit_date="", exit_price=0.0,
                    pnl_pct=0.0, hold_days=0, exit_reason="open",
                )
        self.cash = 0.0


# ============================================================
# 4) Evaluator — 评估 (4 个独立维度)
# ============================================================

class Evaluator(ABC):
    """1 个评估维度 (跟 Strategy 一样 Strategy pattern)"""
    name: str = ""

    @abstractmethod
    def compute(self, nav_history: list[NavPoint], trades: list[Trade]) -> dict[str, float]:
        """返 {metric_name: value}"""
        pass


class ReturnEvaluator(Evaluator):
    """收益维度: 总/年化/日均"""
    name = "收益"

    def compute(self, nav_history, trades):
        if len(nav_history) < 2:
            return {"总收益": 0, "年化": 0, "日均": 0}
        nav0 = nav_history[0].nav
        nav1 = nav_history[-1].nav
        total = nav1 / nav0 - 1
        n_days = len(nav_history)
        annual = (1 + total) ** (252 / n_days) - 1 if total > -1 else -1
        daily = total / n_days
        return {
            "总收益": total,
            "年化": annual,
            "日均": daily,
        }


class RiskEvaluator(Evaluator):
    """风险维度: 最大回撤/波动率/下行波动"""
    name = "风险"

    def compute(self, nav_history, trades):
        if len(nav_history) < 2:
            return {"最大回撤": 0, "年化波动": 0, "下行波动": 0}
        navs = np.array([n.nav for n in nav_history])
        # 算 daily return
        rets = np.diff(navs) / navs[:-1]
        # 最大回撤
        peak = np.maximum.accumulate(navs)
        dd = (navs - peak) / peak
        max_dd = dd.min()
        # 年化波动
        vol = rets.std() * np.sqrt(252)
        # 下行波动 (只算 rets < 0)
        down_vol = rets[rets < 0].std() * np.sqrt(252) if (rets < 0).any() else 0
        return {
            "最大回撤": max_dd,
            "年化波动": vol,
            "下行波动": down_vol,
        }


class RiskAdjReturnEvaluator(Evaluator):
    """风险调整收益: 夏普 / Sortino / Calmar"""
    name = "风险调整收益"

    def compute(self, nav_history, trades):
        if len(nav_history) < 2:
            return {"夏普": 0, "Sortino": 0, "Calmar": 0}
        navs = np.array([n.nav for n in nav_history])
        rets = np.diff(navs) / navs[:-1]
        ann_ret = (navs[-1] / navs[0]) ** (252 / len(rets)) - 1
        ann_vol = rets.std() * np.sqrt(252)
        down = rets[rets < 0]
        down_vol = down.std() * np.sqrt(252) if len(down) > 0 else 1e-9
        # 最大回撤 (复用)
        peak = np.maximum.accumulate(navs)
        dd = (navs - peak) / peak
        max_dd = dd.min()
        # 夏普 (Rf=0 简化)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        # Sortino (用下行波动)
        sortino = ann_ret / down_vol if down_vol > 0 else 0
        # Calmar (年化 / |最大回撤|)
        calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0
        return {
            "夏普": sharpe,
            "Sortino": sortino,
            "Calmar": calmar,
        }


class TradingEvaluator(Evaluator):
    """交易行为: 胜率/盈亏比/平均持仓天数/换手率"""
    name = "交易行为"

    def compute(self, nav_history, trades):
        if not trades:
            return {"胜率": 0, "盈亏比": 0, "平均持仓天数": 0, "总交易数": 0, "换手率": 0}
        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls)
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 0
        profit_ratio = avg_win / avg_loss if avg_loss > 0 else 0
        avg_hold = np.mean([t.hold_days for t in trades])
        # 换手率 = 交易笔数 / 持仓天数 (简化)
        n_hold_days = len(nav_history) if nav_history else 1
        turnover = len(trades) / n_hold_days
        return {
            "胜率": win_rate,
            "盈亏比": profit_ratio,
            "平均持仓天数": avg_hold,
            "总交易数": len(trades),
            "换手率": turnover,
        }
