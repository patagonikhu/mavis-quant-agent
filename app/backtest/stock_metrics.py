"""个股信号回测指标"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StockSignalRecord:
    code: str
    name: str
    date: str           # 信号触发日 YYYY-MM-DD
    signal_type: str    # beichi_new / bsp_new / wyckoff_event_top / wyckoff_event_bot / scene_change / hub_pos_change
    signal_detail: str  # 原始值，如 "1买" / "顶背驰(日线)" / "DistributionStart"
    direction: str      # "buy" | "sell"
    close: float        # 触发日收盘价
    ret_5d: Optional[float] = None
    ret_10d: Optional[float] = None
    ret_20d: Optional[float] = None

    @property
    def is_win_5d(self) -> Optional[bool]:
        if self.ret_5d is None:
            return None
        return self.ret_5d < 0 if self.direction == "sell" else self.ret_5d > 0

    @property
    def is_win_10d(self) -> Optional[bool]:
        if self.ret_10d is None:
            return None
        return self.ret_10d < 0 if self.direction == "sell" else self.ret_10d > 0

    @property
    def is_win_20d(self) -> Optional[bool]:
        if self.ret_20d is None:
            return None
        return self.ret_20d < 0 if self.direction == "sell" else self.ret_20d > 0


@dataclass
class SignalStats:
    signal_type: str
    sample_count: int
    win_rate_5d: float
    win_rate_10d: float
    win_rate_20d: float
    avg_ret_5d: float    # 方向化收益（sell 信号取负值）
    avg_ret_10d: float
    sample_5d: int       # 有效样本数（未到期的排除）
    sample_10d: int
    sample_20d: int


@dataclass
class SignalReport:
    stats: list[SignalStats] = field(default_factory=list)   # win_rate_10d 降序
    total_records: int = 0
    codes_count: int = 0

    @property
    def best_signal(self) -> str:
        valid = [s for s in self.stats if s.sample_10d >= 3]
        return valid[0].signal_type if valid else "—"

    def report_text(self) -> str:
        lines = [
            "=" * 64,
            f"个股信号回测报告  (共 {self.total_records} 条信号 / {self.codes_count} 只股票)",
            "=" * 64,
            f"最优信号 (10d 胜率): {self.best_signal}",
            "",
            f"{'信号类型':<28} {'n5':>4} {'5d%':>6} {'n10':>4} {'10d%':>6} {'n20':>4} {'20d%':>6}  {'平均10d':>8}",
            "-" * 72,
        ]
        for s in self.stats:
            lines.append(
                f"{s.signal_type:<28} "
                f"{s.sample_5d:>4} {s.win_rate_5d*100:>5.0f}% "
                f"{s.sample_10d:>4} {s.win_rate_10d*100:>5.0f}% "
                f"{s.sample_20d:>4} {s.win_rate_20d*100:>5.0f}%  "
                f"{s.avg_ret_10d*100:>+7.1f}%"
            )
        lines += ["", "⚠  回测结果仅供研究参考，不构成投资建议。"]
        return "\n".join(lines)
