"""
tools/batch_runner.py — 统一批跑框架 (2026-09-01)

把"并发 + 进度 + 错误处理"抽出来, 业务逻辑 (worker_fn) 各自实现。

用法:
    from tools.batch_runner import run_batch, compute_exit_code

    results = run_batch(
        items=watchlist,
        worker_fn=lambda s: process_one(s),
        workers=4,
        desc="分析全 watchlist",
    )
    sys.exit(compute_exit_code(results))

特性:
    1. 进度条: 每 10% 打印一次
    2. 错误隔离: 单只失败收进 results['failed'], 不影响整体
    3. 限流: rate_limit_per_min 自动 sleep
    4. Ctrl-C 友好: KeyboardInterrupt 优雅退出, 打印已完成
    5. 退出码: 0 (>=95% ok) / 2 (部分失败) / 0 (空)

替代 12+ 个 batch 脚本各自重复的 ThreadPoolExecutor 块 (15-20 行/处)。
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional


def run_batch(
    items: list,
    worker_fn: Callable[[Any], Any],
    workers: int = 4,
    desc: str = "Batch",
    on_result: Optional[Callable[[Any, Any], None]] = None,
    on_error: Optional[Callable[[Any, Exception], bool]] = None,
    rate_limit_per_min: Optional[int] = None,
) -> dict:
    """并发跑 items, 错误隔离 + 进度 + 限流

    Args:
        items: 待处理列表 (codes / stocks / ...)
        worker_fn: 单只处理函数 (item) -> result, 异常会被捕获
        workers: 并发数, 默认 4
        desc: 进度描述
        on_result: 每只成功后回调 (item, result) -> None, 用于实时写盘/打印
        on_error: 异常回调 (item, exc) -> bool, 返 True 重试 1 次, False 跳过
        rate_limit_per_min: 限流 (如 200), 自动 sleep 60/N 秒/次

    Returns:
        {
            "ok": [(item, result), ...],
            "failed": [(item, exception), ...],
            "total": int,
            "elapsed_sec": float,
        }
    """
    results: dict = {"ok": [], "failed": [], "total": len(items), "elapsed_sec": 0.0}
    if not items:
        return results

    t0 = time.time()
    sleep_per_call = (60.0 / rate_limit_per_min) if rate_limit_per_min else 0
    n_total = len(items)
    progress_step = max(1, n_total // 10)  # 每 10% 打印一次
    last_printed_pct = 0

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(worker_fn, item): item for item in items}
            for i, fut in enumerate(as_completed(futs), 1):
                item = futs[fut]
                try:
                    result = fut.result()
                    results["ok"].append((item, result))
                    if on_result:
                        on_result(item, result)
                except Exception as e:
                    # 可选重试
                    if on_error and on_error(item, e):
                        try:
                            result = worker_fn(item)
                            results["ok"].append((item, result))
                        except Exception as e2:
                            results["failed"].append((item, e2))
                    else:
                        results["failed"].append((item, e))
                # 进度
                if i % progress_step == 0 or i == n_total:
                    pct = i * 100 // n_total
                    if pct >= last_printed_pct + 10 or i == n_total:
                        print(f"  [{i}/{n_total}] {desc} {pct}%", flush=True)
                        last_printed_pct = pct
                # 限流
                if sleep_per_call:
                    time.sleep(sleep_per_call)
    except KeyboardInterrupt:
        print(f"\n⚠️ 用户中断, 已完成 {len(results['ok'])}/{n_total}", file=sys.stderr)
        results["failed"].append(("__INTERRUPT__", KeyboardInterrupt("用户中断")))

    results["elapsed_sec"] = round(time.time() - t0, 1)
    print(
        f"  ✅ {desc} 完成: {len(results['ok'])} ok / {len(results['failed'])} failed / "
        f"{results['elapsed_sec']}s",
        flush=True,
    )
    return results


def compute_exit_code(
    results: dict,
    ok_threshold: float = 0.95,
    partial_threshold: float = 0.80,
) -> int:
    """根据结果算退出码

    Returns:
        0: 成功率 >= ok_threshold (默认 95%)
        2: partial_threshold <= 成功率 < ok_threshold (默认 80-95%)
        2: 成功率 < partial_threshold (也返 2, 提示用户重跑)
        0: 空 results (没东西要跑)
    """
    total = results.get("total", 0)
    if total == 0:
        return 0
    rate = len(results.get("ok", [])) / total
    if rate >= ok_threshold:
        return 0
    return 2
