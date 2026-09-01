"""个股信号回测引擎

复用 analyze_history → compute_factor_history → diff_rows,
对 watchlist 每只股票逐日切片算信号, 统计各信号触发后 5d/10d/20d 胜率。

用法:
    from app.backtest.stock_engine import StockBacktestEngine
    engine = StockBacktestEngine(lookback_days=90, hold_days=10)
    records = engine.run(codes=["002371", "688012"])
    report  = engine.report(records)
    print(report.report_text())
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Optional

from app.backtest.stock_metrics import SignalReport, SignalStats, StockSignalRecord
from tools.analysis.analysis_result_signals import extract_signals as _extract_signals

logger = logging.getLogger(__name__)

WATCHLIST_PATH = Path("data/watchlist.json")
WITH_VENV = Path("tools/with_venv.sh")


# ── 收益计算 ──────────────────────────────────────────────────────────────

def _calc_returns(kline: list[dict], signal_idx: int, hold_days: int) -> tuple:
    """返回 (ret_5d, ret_10d, ret_20d)，不足则为 None"""
    base = kline[signal_idx]["close"]
    if base <= 0:
        return None, None, None

    def _ret(n):
        idx = signal_idx + n
        return (kline[idx]["close"] - base) / base if idx < len(kline) else None

    return _ret(5), _ret(10), _ret(20)


# ── 主引擎 ────────────────────────────────────────────────────────────────

class StockBacktestEngine:

    def __init__(
        self,
        lookback_days: int = 90,
        hold_days: int = 10,
        force_dump: bool = False,
    ):
        self.lookback_days = lookback_days
        self.hold_days     = hold_days
        self.force_dump    = force_dump

    # ── 数据加载 ──────────────────────────────────────────────────────────

    def _load_watchlist(self) -> list[dict]:
        with open(WATCHLIST_PATH) as f:
            data = json.load(f)
        return data.get("stocks", [])

    def _dump_code(self, code: str) -> None:
        logger.info("强制重拉 dump: %s", code)
        subprocess.run(
            ["bash", str(WITH_VENV), "python", "-m", "tools.sync_watchlist_fresh", code],
            check=True,
        )

    def _load_dump(self, code: str) -> Optional["RawContext"]:
        from tools.kline_store import DataStore
        ctx = DataStore.get_ctx(code)
        if not ctx.kline:
            logger.warning("DataStore 无K线: %s，跳过", code)
            return None
        return ctx

    # ── 核心：单只股票回测 ────────────────────────────────────────────────

    def _run_one(self, code: str, name: str) -> list[StockSignalRecord]:
        from tools.analysis.analysis_result_signals import compute_factor_history, diff_rows
        from tools.analysis.analysis_engine import AnalysisEngine

        ctx = self._load_dump(code)
        if ctx is None:
            return []

        kline = ctx.kline
        if len(kline) < 40:
            logger.warning("%s kline 不足 40 条，跳过", code)
            return []

        # 建立 date→kline_index 映射（用于收益计算）
        date_to_idx = {bar["trade_date"]: i for i, bar in enumerate(kline)}

        # 复用 engine.analyze_history 结果 (跟 t-analyze-all 模式一致)
        try:
            all_dates = [k['trade_date'].replace('-','')[:8] for k in kline]
            lookback_dates = all_dates[-self.lookback_days:]
            history = AnalysisEngine().analyze_history(ctx, lookback_dates)
            rows = compute_factor_history(ctx, step=1, lookback=self.lookback_days, history=history)
        except Exception as e:
            logger.error("%s compute_factor_history 失败: %s", code, e)
            return []

        records: list[StockSignalRecord] = []

        for i in range(1, len(rows)):
            changes = diff_rows(rows[i - 1], rows[i])
            if not changes:
                continue

            signals = _extract_signals(changes)
            if not signals:
                continue

            signal_date = rows[i]["date"]
            close       = rows[i]["close"]
            idx         = date_to_idx.get(signal_date)
            if idx is None:
                continue

            ret_5d, ret_10d, ret_20d = _calc_returns(kline, idx, self.hold_days)

            for sig_type, sig_detail, direction in signals:
                records.append(StockSignalRecord(
                    code=code,
                    name=name,
                    date=signal_date,
                    signal_type=sig_type,
                    signal_detail=sig_detail,
                    direction=direction,
                    close=close,
                    ret_5d=ret_5d,
                    ret_10d=ret_10d,
                    ret_20d=ret_20d,
                ))

        logger.info("%s %s: %d 条信号", code, name, len(records))
        return records

    # ── 公开接口 ──────────────────────────────────────────────────────────

    def run(self, codes: Optional[list[str]] = None) -> list[StockSignalRecord]:
        """运行回测。codes=None 时读 data/watchlist.json 全量。"""
        if codes:
            stocks = [{"code": c, "name": c} for c in codes]
        else:
            stocks = self._load_watchlist()

        all_records: list[StockSignalRecord] = []
        for s in stocks:
            code = s["code"]
            name = s.get("name", code)
            if self.force_dump:
                try:
                    self._dump_code(code)
                except Exception as e:
                    logger.error("dump %s 失败: %s，继续", code, e)
            all_records.extend(self._run_one(code, name))

        logger.info("回测完成，共 %d 条信号记录", len(all_records))
        return all_records

    def report(self, records: list[StockSignalRecord]) -> SignalReport:
        """按信号类型汇总胜率，返回 SignalReport。"""
        if not records:
            return SignalReport(total_records=0, codes_count=0)

        # 按 signal_type 分组
        groups: dict[str, list[StockSignalRecord]] = defaultdict(list)
        for r in records:
            groups[r.signal_type].append(r)

        stats_list: list[SignalStats] = []
        for sig_type, recs in groups.items():
            def _rate(wins, n):
                return wins / n if n else 0.0

            w5  = [r for r in recs if r.is_win_5d  is not None]
            w10 = [r for r in recs if r.is_win_10d is not None]
            w20 = [r for r in recs if r.is_win_20d is not None]

            # 方向化平均收益（sell 信号收益取反，正数=对方向）
            def _dir_ret(r, attr):
                v = getattr(r, attr)
                if v is None:
                    return None
                return -v if r.direction == "sell" else v

            avg5  = mean(_dir_ret(r, "ret_5d")  for r in w5)  if w5  else 0.0
            avg10 = mean(_dir_ret(r, "ret_10d") for r in w10) if w10 else 0.0

            stats_list.append(SignalStats(
                signal_type  = sig_type,
                sample_count = len(recs),
                win_rate_5d  = _rate(sum(1 for r in w5  if r.is_win_5d),  len(w5)),
                win_rate_10d = _rate(sum(1 for r in w10 if r.is_win_10d), len(w10)),
                win_rate_20d = _rate(sum(1 for r in w20 if r.is_win_20d), len(w20)),
                avg_ret_5d   = avg5,
                avg_ret_10d  = avg10,
                sample_5d    = len(w5),
                sample_10d   = len(w10),
                sample_20d   = len(w20),
            ))

        stats_list.sort(key=lambda s: s.win_rate_10d, reverse=True)

        return SignalReport(
            stats         = stats_list,
            total_records = len(records),
            codes_count   = len({r.code for r in records}),
        )
