"""信号权重参数自动优化

网格搜索信号权重组合，找到使样本内回测胜率最高的配置。
结果写入 data/best_params.json，scorer.py 启动时自动加载。

设计思路：
  - 对 SIGNAL_WEIGHTS 里各大类（量价/龙头/资金/政策/情绪）做缩放系数搜索
  - 缩放系数范围 [0.5, 1.0, 1.5, 2.0]，共 4^5 = 1024 种组合
  - 实际跑回测取胜率和涨幅最优的组合
"""

from __future__ import annotations

import datetime
import itertools
import json
import logging
from pathlib import Path
from typing import Optional

from app.signals.scorer import SIGNAL_WEIGHTS

logger = logging.getLogger(__name__)

BEST_PARAMS_PATH = Path("data/best_params.json")

# 5大类信号分组
SIGNAL_GROUPS = {
    "volume_price": ["volume_breakout", "limit_up_surge", "volume_price_uptrend", "breakout_resistance"],
    "leader":       ["leader_launching", "sector_diffusion"],
    "capital":      ["main_capital_inflow", "north_capital_anomaly", "institutional_concentration"],
    "policy":       ["policy_keyword_hit", "llm_policy_score"],
    "sentiment":    ["hot_rank_surge", "research_surge", "discussion_anomaly"],
}

# 每组缩放系数候选值
SCALE_CANDIDATES = [0.5, 1.0, 1.5, 2.0]


def _apply_scales(scales: dict[str, float]) -> dict[str, float]:
    """将大类缩放系数应用到各信号权重"""
    new_weights: dict[str, float] = {}
    for group, signals in SIGNAL_GROUPS.items():
        scale = scales.get(group, 1.0)
        for sig in signals:
            new_weights[sig] = SIGNAL_WEIGHTS.get(sig, 0.0) * scale
    return new_weights


async def optimize_weights(
    start: datetime.date,
    end: datetime.date,
    sectors: Optional[list[str]] = None,
    save: bool = True,
) -> dict:
    """网格搜索最优权重

    Args:
        start: 训练开始日期
        end: 训练结束日期
        sectors: 要优化的板块列表
        save: 是否将最优权重写入 data/best_params.json

    Returns:
        {best_weights, best_win_rate, best_avg_return, all_results, saved}
    """
    from app.backtest.engine import BacktestEngine
    from app.backtest.metrics import calculate_metrics
    from app.data.models import SECTOR_LIST

    targets = sectors or SECTOR_LIST[:3]
    engine = BacktestEngine(start, end, min_score_to_record=20.0)

    group_names = list(SIGNAL_GROUPS.keys())
    combos = list(itertools.product(SCALE_CANDIDATES, repeat=len(group_names)))

    logger.info("开始参数优化：%d 种组合，板块 %s", len(combos), targets)

    best_score = -1.0
    best_weights: dict[str, float] = dict(SIGNAL_WEIGHTS)
    best_win_rate = 0.0
    best_avg_return = 0.0
    all_results = []

    for i, combo in enumerate(combos):
        scales = dict(zip(group_names, combo))
        weights = _apply_scales(scales)

        # 用当前权重临时覆盖 scorer
        _patch_weights(weights)
        try:
            records = await engine.run(targets)
            metrics = calculate_metrics(records)
        except Exception as e:
            logger.warning("组合 %d 失败: %s", i, e)
            continue
        finally:
            _restore_weights()

        if metrics.total_signals == 0:
            continue

        # 目标函数：胜率 * 0.6 + 归一化涨幅 * 0.4
        obj = metrics.win_rate * 0.6 + min(metrics.avg_max_return * 5, 1.0) * 0.4
        all_results.append({
            "scales": scales,
            "win_rate": round(metrics.win_rate, 3),
            "avg_return": round(metrics.avg_max_return, 4),
            "total_signals": metrics.total_signals,
            "objective": round(obj, 4),
        })

        if obj > best_score:
            best_score = obj
            best_weights = weights
            best_win_rate = metrics.win_rate
            best_avg_return = metrics.avg_max_return
            logger.info(
                "  新最优（组合%d）: 胜率=%.1f%% 涨幅=%.1f%% scales=%s",
                i, metrics.win_rate * 100, metrics.avg_max_return * 100, scales,
            )

    saved = False
    if save and best_weights:
        BEST_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "weights": best_weights,
            "win_rate": best_win_rate,
            "avg_return": best_avg_return,
            "optimized_on": str(datetime.date.today()),
        }
        BEST_PARAMS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        saved = True
        logger.info("最优权重已保存: %s", BEST_PARAMS_PATH)

    return {
        "best_weights": best_weights,
        "best_win_rate": best_win_rate,
        "best_avg_return": best_avg_return,
        "combos_tested": len(all_results),
        "saved": saved,
        "top5": sorted(all_results, key=lambda x: x["objective"], reverse=True)[:5],
    }


def load_best_weights() -> Optional[dict[str, float]]:
    """加载已保存的最优权重，不存在则返回 None"""
    if not BEST_PARAMS_PATH.exists():
        return None
    try:
        data = json.loads(BEST_PARAMS_PATH.read_text())
        weights = data.get("weights")
        if weights:
            logger.info(
                "加载最优权重（优化于 %s，历史胜率 %.1f%%）",
                data.get("optimized_on", "未知"),
                data.get("win_rate", 0) * 100,
            )
        return weights
    except Exception as e:
        logger.warning("加载最优权重失败: %s", e)
        return None


# ---- 临时权重 patch（线程不安全，仅用于单线程优化）----

_original_weights: Optional[dict] = None


def _patch_weights(weights: dict[str, float]) -> None:
    global _original_weights
    import app.signals.scorer as scorer_mod
    _original_weights = dict(scorer_mod.SIGNAL_WEIGHTS)
    scorer_mod.SIGNAL_WEIGHTS.clear()
    scorer_mod.SIGNAL_WEIGHTS.update(weights)
    scorer_mod._MAX_SCORE = sum(weights.values()) or 1.0


def _restore_weights() -> None:
    global _original_weights
    if _original_weights is not None:
        import app.signals.scorer as scorer_mod
        scorer_mod.SIGNAL_WEIGHTS.clear()
        scorer_mod.SIGNAL_WEIGHTS.update(_original_weights)
        scorer_mod._MAX_SCORE = sum(_original_weights.values()) or 1.0
        _original_weights = None
