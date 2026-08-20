"""用户反馈收集 + A/B 权重测试

Phase 5 核心功能：
1. FeedbackStore：存储用户对信号的评价（good/bad/neutral），持久化到 SQLite
2. ABTestRunner：两组权重并行运行，按信号评分自动分桶，统计胜率差异

使用方式：
    # 记录反馈
    store = FeedbackStore()
    await store.add("半导体", score=72.0, rating="好信号", user_comment="确实涨了")

    # A/B 测试
    runner = ABTestRunner(weights_a=DEFAULT_WEIGHTS, weights_b=TUNED_WEIGHTS)
    await runner.run(sectors=["半导体", "AI"], days=30)
    print(runner.report())
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("data/feedback.db")
FeedbackRating = Literal["good", "bad", "neutral"]


class FeedbackStore:
    """用户反馈持久化存储（SQLite）"""

    def __init__(self, db_path: Path = DB_PATH):
        self._db = db_path
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    created   TEXT    NOT NULL,
                    sector    TEXT    NOT NULL,
                    score     REAL    NOT NULL,
                    rating    TEXT    NOT NULL,
                    comment   TEXT    DEFAULT '',
                    signals   TEXT    DEFAULT '[]',
                    outcome   REAL    DEFAULT NULL
                )
            """)
            conn.commit()

    async def add(
        self,
        sector_name: str,
        score: float,
        rating: FeedbackRating,
        comment: str = "",
        triggered_signals: Optional[list[str]] = None,
    ) -> int:
        """记录一条反馈

        Returns:
            新记录的 id
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._insert,
            sector_name, score, rating, comment, triggered_signals or [],
        )

    def _insert(
        self,
        sector_name: str,
        score: float,
        rating: str,
        comment: str,
        signals: list[str],
    ) -> int:
        with sqlite3.connect(self._db) as conn:
            cur = conn.execute(
                "INSERT INTO feedback (created, sector, score, rating, comment, signals) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.datetime.now().isoformat(timespec="seconds"),
                    sector_name, score, rating, comment,
                    json.dumps(signals, ensure_ascii=False),
                ),
            )
            conn.commit()
            return cur.lastrowid

    async def update_outcome(self, feedback_id: int, actual_return: float) -> None:
        """补录信号后实际涨幅（事后验证）"""
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._run_sql(
                "UPDATE feedback SET outcome=? WHERE id=?",
                (actual_return, feedback_id),
            ),
        )

    def _run_sql(self, sql: str, params: tuple = ()) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(sql, params)
            conn.commit()

    async def list_recent(self, limit: int = 20) -> list[dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_recent, limit)

    def _list_recent(self, limit: int) -> list[dict]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "date": r["created"],
                    "sector_name": r["sector"],
                    "score": r["score"],
                    "rating": r["rating"],
                    "comment": r["comment"],
                    "outcome": r["outcome"],
                }
                for r in rows
            ]

    async def get_stats(self) -> dict:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_stats)

    def _get_stats(self) -> dict:
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute(
                "SELECT rating, COUNT(*) as cnt FROM feedback GROUP BY rating"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            accuracy_row = conn.execute(
                "SELECT AVG(outcome) FROM feedback WHERE outcome IS NOT NULL AND rating='good'"
            ).fetchone()

        counts = {r[0]: r[1] for r in rows}
        good_avg_outcome = accuracy_row[0] if accuracy_row and accuracy_row[0] is not None else None

        return {
            "total": total,
            "good": counts.get("good", 0),
            "bad": counts.get("bad", 0),
            "neutral": counts.get("neutral", 0),
            "good_signal_avg_actual_return": (
                f"{good_avg_outcome*100:.1f}%" if good_avg_outcome is not None else "N/A"
            ),
        }


class ABTestRunner:
    """A/B 权重测试

    两组权重并行在同一历史数据上运行，比较指标差异。
    A 组通常是当前默认权重，B 组是候选优化权重。
    """

    def __init__(
        self,
        weights_a: Optional[dict[str, float]] = None,
        weights_b: Optional[dict[str, float]] = None,
        name_a: str = "默认权重",
        name_b: str = "优化权重",
    ):
        from app.signals.scorer import SIGNAL_WEIGHTS
        self.weights_a = weights_a or dict(SIGNAL_WEIGHTS)
        self.weights_b = weights_b or dict(SIGNAL_WEIGHTS)
        self.name_a = name_a
        self.name_b = name_b
        self._metrics_a = None
        self._metrics_b = None

    async def run(
        self,
        start: datetime.date,
        end: datetime.date,
        sectors: Optional[list[str]] = None,
    ) -> None:
        from app.backtest.engine import BacktestEngine
        from app.backtest.metrics import calculate_metrics
        from app.backtest.optimizer import _patch_weights, _restore_weights
        from app.data.models import SECTOR_LIST

        targets = sectors or SECTOR_LIST[:3]
        engine = BacktestEngine(start, end)

        # 运行 A
        _patch_weights(self.weights_a)
        try:
            records_a = await engine.run(targets)
            self._metrics_a = calculate_metrics(records_a)
        finally:
            _restore_weights()

        # 运行 B
        _patch_weights(self.weights_b)
        try:
            records_b = await engine.run(targets)
            self._metrics_b = calculate_metrics(records_b)
        finally:
            _restore_weights()

    def report(self) -> str:
        if self._metrics_a is None or self._metrics_b is None:
            return "尚未运行，请先调用 run()"

        ma, mb = self._metrics_a, self._metrics_b

        def _delta(a: float, b: float) -> str:
            d = b - a
            return f"{'+' if d >= 0 else ''}{d*100:.1f}pp"

        lines = [
            "=" * 52,
            f"A/B 权重测试报告",
            "=" * 52,
            f"{'指标':<20} {'A: '+self.name_a:>16} {'B: '+self.name_b:>16}  {'B-A':>8}",
            "-" * 52,
            f"{'信号总数':<20} {ma.total_signals:>16} {mb.total_signals:>16}",
            f"{'胜率':<20} {ma.win_rate*100:>15.1f}% {mb.win_rate*100:>15.1f}%  {_delta(ma.win_rate, mb.win_rate):>8}",
            f"{'强信号胜率':<20} {ma.win_rate_strong*100:>15.1f}% {mb.win_rate_strong*100:>15.1f}%  {_delta(ma.win_rate_strong, mb.win_rate_strong):>8}",
            f"{'平均最大涨幅':<20} {ma.avg_max_return*100:>15.1f}% {mb.avg_max_return*100:>15.1f}%  {_delta(ma.avg_max_return, mb.avg_max_return):>8}",
            f"{'夏普比率':<20} {ma.sharpe_ratio:>16.2f} {mb.sharpe_ratio:>16.2f}",
            f"{'最大回撤':<20} {ma.max_drawdown*100:>15.1f}% {mb.max_drawdown*100:>15.1f}%",
            "-" * 52,
        ]

        winner = self.name_b if mb.win_rate > ma.win_rate else self.name_a
        lines.append(f"综合胜率更优：{winner}")

        return "\n".join(lines)
