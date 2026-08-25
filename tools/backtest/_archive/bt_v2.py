"""
/tmp/bt_v2.py — 干净快速版: K 线直接算 BOLL+MA120, factor_history 算信号

策略:
  - 阶段 1: 用 K 线快速筛出触底行 (不调 factor_history, 1ms/票)
  - 阶段 2: 只对触底票算 factor_history (缠论+MACD) — 节省 80% 时间
"""
import sys, time, statistics
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, '/Users/I514959/workspace/mavis-quant-agent')

SAMPLE = ['688012', '688361', '688072', '688082', '688037', '688041', '688256',
          '002371', '300604', '603290', '002028', '600089', '300274', '300308',
          '300502', '300223', '300285', '601138']

LOOKBACK = 180
BOLL_THRESHOLDS = [10, 15]
MA120_MIN, MA120_MAX = -5, 15
WINDOW_DAYS = 5
WIN_DAYS = 30


def quick_boll_ma120(closes, boll):
    """快速找出所有触底行 (布林+MA120), 返回 [(i, boll_pct, ma120_dev)]"""
    touch_idxs = []
    n = len(closes)
    for i in range(120, n):
        # BOLL 20
        win = closes[i - 19:i + 1]
        mid = sum(win) / 20
        std = (sum((x - mid) ** 2 for x in win) / 20) ** 0.5
        if std <= 0:
            continue
        bp = (closes[i] - (mid - 2 * std)) / (4 * std) * 100
        # MA120
        ma120 = sum(closes[i - 119:i + 1]) / 120
        dev = (closes[i] / ma120 - 1) * 100

        if not (0 <= bp <= boll):
            continue
        if not (MA120_MIN <= dev <= MA120_MAX):
            continue
        touch_idxs.append((i, bp, dev))
    return touch_idxs


def scan_one(args):
    code, boll, mode = args
    try:
        from tools.history_sync import read_kline
        from tools.data_store import _to_ts_code
        rows = read_kline(_to_ts_code(code), limit=300)
        closes = [r['close'] for r in rows if r.get('close', 0) > 0]
        if len(closes) < 130:
            return []

        # 阶段 1: 快速筛触底
        touchs = quick_boll_ma120(closes, boll)
        if not touchs:
            return []

        # 阶段 2: 对触底行算 factor_history
        from tools.data_store import DataStore
        from tools.analysis.factor_history import compute_factor_history
        from tools.analysis.analysis_engine import ChanStrategy, MacdDivergenceStrategy
        ctx = DataStore.get_ctx(code, kline_only=True, limit=300)
        fh_rows = compute_factor_history(ctx, step=1, lookback=LOOKBACK,
                                          strategies=[ChanStrategy, MacdDivergenceStrategy])
        if not fh_rows:
            return []

        # 对齐: fh_rows 跟原 rows 长度一致 (默认 lookback=180 ≈ 全部数据)
        # 因为 LOOKBACK=180, 跟 factor_history 默认 lookback 一致, 索引应该对齐
        if len(fh_rows) != len(rows):
            offset = len(rows) - len(fh_rows)
        else:
            offset = 0

        # 30d 去重
        signals = []
        last_trigger = -30
        for i, bp, dev in touchs:
            if i - last_trigger < 30:
                continue

            # 5d 内 2 次触底
            has_double = any(
                abs(j - i) <= WINDOW_DAYS and j != i
                for j, _, _ in touchs
                if abs(j - i) <= WINDOW_DAYS
            )
            if not has_double:
                continue

            # 算缠论/MACD
            fh_i = i - offset
            if fh_i < 0 or fh_i >= len(fh_rows):
                continue
            fh_r = fh_rows[fh_i]

            # 缠论
            has_c = False
            start = max(0, fh_i - 30)
            for r in fh_rows[start:fh_i + 1]:
                bc = r.get("daily_beichi") or {}
                if isinstance(bc, dict):
                    if bc.get("direction") == "bot" and bc.get("strength") in ("strong", "weak"):
                        has_c = True
                        break

            # MACD
            has_m = any(
                r.get("macd_div_bot")
                for r in fh_rows[max(0, fh_i - 5):fh_i + 1]
            )

            if mode == 'dual' and not (has_c or has_m):
                continue
            if mode == 'triple' and not (has_c and has_m):
                continue

            # 30d 窗口
            if i + 30 >= len(closes):
                continue
            window = closes[i + 1:i + 31]
            c0 = closes[i]
            max_up = (max(window) - c0) / c0 * 100
            fin = (window[-1] - c0) / c0 * 100
            signals.append({'i': i, 'max': max_up, 'fin': fin})
            last_trigger = i

        return [(code, s) for s in signals]
    except Exception:
        return []


def main():
    print("=" * 90)
    print(f"  快速回测 (K 线直接算 BOLL, factor_history 只对触底行算信号)")
    print("=" * 90)

    # 18 只
    t0 = time.time()
    results_by_cfg = {}
    for boll in [10, 15]:
        for mode in ['dual', 'triple']:
            cfg = (boll, mode)
            signals = []
            with ProcessPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(scan_one, (c, boll, mode)): c for c in SAMPLE}
                for fut in as_completed(futs):
                    r = fut.result()
                    if r:
                        signals.extend(r)
            results_by_cfg[cfg] = signals
    elapsed = time.time() - t0
    print(f"\n⏱️  18 只 × 4 配置 = 72 任务, 耗时 {elapsed:.1f}s")

    # 全市场
    from tools.data_store import DataStore
    all_codes = DataStore.list_codes()
    print(f"\n跑全市场 {len(all_codes)} 只 × 4 配置 = {len(all_codes)*4} 任务...")
    t0 = time.time()
    for boll in [10, 15]:
        for mode in ['dual', 'triple']:
            cfg = (boll, mode)
            signals = []
            with ProcessPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(scan_one, (c, boll, mode)): c for c in all_codes}
                done = 0
                for fut in as_completed(futs):
                    r = fut.result()
                    if r:
                        signals.extend(r)
                    done += 1
                    if done % 1000 == 0:
                        print(f"  {cfg}: {done}/{len(all_codes)} ({done/len(all_codes)*100:.0f}%)  {time.time()-t0:.0f}s")
            results_by_cfg[cfg] = signals
    print(f"⏱️  全市场完成, 耗时 {time.time()-t0:.0f}s")

    # 输出
    print(f"\n{'='*90}")
    print(f"  30d 窗口 max_upside 胜率")
    print(f"{'='*90}")
    print(f"{'配置':<25}{'样本':<8}{'n':<6}{'>0%':<8}{'>5%':<8}{'>10%':<8}{'>15%':<8}{'>20%':<8}{'>30%':<8}{'均max':<10}")
    print("-" * 110)
    for cfg, signals in results_by_cfg.items():
        boll, mode = cfg
        for label, codes_n in [('18只', 18), ('全市场', len(all_codes))]:
            cfg_label = f"{label} 布林≤{boll}% + {mode}"
            if not signals:
                continue
            n = len(signals)
            max_vals = [s[1]['max'] for s in signals]
            line = f"{cfg_label:<25}{label:<8}{n:<6}"
            for thr in [0, 5, 10, 15, 20, 30]:
                wins = sum(1 for m in max_vals if m > thr)
                line += f"{wins/n*100:>5.1f}%   "
            line += f"{statistics.mean(max_vals):>+6.1f}%"
            print(line)


if __name__ == "__main__":
    main()
