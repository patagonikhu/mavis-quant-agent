#!/usr/bin/env python3
"""RSI6 超卖反弹回测 — 多阈值对比

策略: RSI6 < threshold 当日, 次日开盘买入, 持有 N 天
阈值扫描: < 5 / < 10 / < 15 / < 20 / < 30

2026-09-22 加: --yoy-strict 复用 /t-rsi6-tech 6 重业绩过滤 (业绩差 → 排除)
   1. 本季净利 yoy > 0
   2. 上季净利 yoy > 0 (连续两季盈利)
   3. 营收 yoy >= -10%
   4. 净利 yoy 边际放缓 >= -10pp (避免断崖)
   默认关闭 (向后兼容老用法); 加 --yoy-strict 启用
   启用后: 6 重过滤 = RSI6 < thr + 4 重业绩 (本脚本没 RSI12/thr)
"""
import argparse
import sys
import time
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.storage.store import DataStore


def get_kline(code, limit=1250):
    rows = DataStore.get_kline(code, limit=limit)
    if not rows: return None
    df = pd.DataFrame(rows)
    df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '').str[:8]
    return df.sort_values('trade_date').reset_index(drop=True)


def rsi6(closes):
    """Wilder's RSI(6), 前 6 位置 NaN"""
    n = len(closes)
    if n < 7: return np.full(n, np.nan)
    diff = np.diff(closes, prepend=closes[0])
    up = np.where(diff > 0, diff, 0.0)
    dn = np.where(diff < 0, -diff, 0.0)
    rsi = np.full(n, np.nan)
    # Wilder smoothing
    avg_up = up[:6].mean()
    avg_dn = dn[:6].mean()
    rs = avg_up / avg_dn if avg_dn > 0 else 100
    rsi[5] = 100 - 100 / (1 + rs)
    for i in range(6, n):
        avg_up = (avg_up * 5 + up[i]) / 6
        avg_dn = (avg_dn * 5 + dn[i]) / 6
        rs = avg_up / avg_dn if avg_dn > 0 else 100
        rsi[i] = 100 - 100 / (1 + rs)
    return rsi


# 2026-09-22 加: /t-rsi6-tech 4 重业绩过滤 helper (从 rsi6_tech_scan 复用)
def _load_yoy_map() -> dict:
    """加载最新季报 yoy 映射 (复用 rsi6_tech_scan._load_yoy_map)."""
    try:
        from tools.batch.rsi6_tech_scan import _load_yoy_map as _src
        return _src()
    except Exception as e:
        print(f"  ⚠️ yoy 加载失败: {e}")
        return {}


def _passes_yoy_filter(yoy: dict) -> bool:
    """4 重业绩过滤 (本季 + 上季 yoy > 0, 营收 yoy >= -10%, 边际放缓 >= -10pp)

    与 /t-rsi6-tech 6 重过滤中后 4 重一致 (前 2 重是 RSI6/RSI12 已在外层)
    yoy 缺失 → 视为不通过 (保守)
    """
    if not yoy:
        return False
    np_yoy = yoy.get("np_yoy")
    rev_yoy = yoy.get("rev_yoy")
    np_yoy_prev = yoy.get("np_yoy_prev")
    if np_yoy is None or np_yoy <= 0:
        return False
    if np_yoy_prev is None or np_yoy_prev <= 0:
        return False
    if rev_yoy is not None and rev_yoy < -10:
        return False
    if np_yoy is not None and np_yoy_prev is not None:
        if (np_yoy - np_yoy_prev) < -10:
            return False
    return True


def main():
    parser = argparse.ArgumentParser()
    # 2026-09-22 改: hold-days 默认 30 (从 20 升, 用户回测 30 天持有)
    parser.add_argument('--hold-days', type=int, default=30)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--thresholds', type=str, default='5,10,15,20,30')
    parser.add_argument('--write-md', action='store_true')
    # 2026-09-22 加: --write-trades 写每笔明细到 parquet (后续失败分析用)
    parser.add_argument('--write-trades', action='store_true',
                        help='每笔 trade 写 parquet (data/rsi6_backtest_trades.parquet)')
    # 2026-09-22 加: --yoy-strict 启用 /t-rsi6-tech 4 重业绩过滤
    parser.add_argument('--yoy-strict', action='store_true',
                        help='启用 /t-rsi6-tech 业绩过滤 (净利yoy>0 + 净利yoy_prev>0 + 营收yoy>=-10% + 边际放缓>=10pp)')
    # 2026-09-22 加: --include-st 默认排除 ST 票 (雪球/退市风险)
    parser.add_argument('--include-st', action='store_true',
                        help='包含 ST 票 (默认排除, ST 票大幅拉低胜率)')
    args = parser.parse_args()

    thresholds = [int(t) for t in args.thresholds.split(',')]

    basic = pd.read_parquet('data/history/stock_basic/stock_basic.parquet')
    all_codes = basic['ts_code'].str[:6].tolist()
    if args.limit:
        all_codes = all_codes[:args.limit]
    print(f'RSI6 回测 | {len(all_codes)} 只 | 持有 {args.hold_days} 天 | 阈值 {thresholds}')

    # 先一次性算每只的 RSI6 (避免重复算)
    code_rsi = {}
    t0 = time.time()
    for ci, code in enumerate(all_codes):
        if ci % 500 == 0:
            print(f'  [{ci}/{len(all_codes)}] 耗时 {time.time()-t0:.0f}s', flush=True)
        df = get_kline(code)
        if df is None or len(df) < 60: continue
        closes = df['close'].values
        dates = df['trade_date'].values
        rsi = rsi6(closes)
        code_rsi[code] = (closes, dates, rsi)

    print(f'RSI 预算完成: {len(code_rsi)} 只 ({time.time()-t0:.0f}s)\n')

    # 2026-09-22 加: 行业映射 (用于 trade 明细 + 失败分析)
    industry_map = {}
    if args.write_trades:
        from tools.batch.rsi6_tech_scan import _load_industry_map
        industry_map = _load_industry_map(list(code_rsi.keys()))

    # 2026-09-22 加: ST 票集合 (默认排除, --include-st 包含)
    #   ST 票在回测中触发大幅亏损 (002731 *ST 萃华 单票贡献 70%+ 大亏)
    #   当前快照 vs 历史: 2024Q1 业绩当时快照没 ST, 但 2026Q3 已被 ST
    #   用当前 name 是否含 ST 来粗筛, 不能完美但能挡掉现行 ST 大坑
    st_codes = set()
    if not args.include_st:
        try:
            from tools.storage.store import DataStore as _DS
            _sb = _DS.load_stock_basic()
            st_codes = set(_sb[_sb['name'].str.contains('ST', na=False)]['code'].tolist())
            print(f'ST 过滤启用: 排除 {len(st_codes)} 只 (当前快照)')
        except Exception as e:
            print(f'  ⚠️ ST 加载失败: {e}')
    print()

    # 2026-09-22 加: --yoy-strict 时加载业绩映射
    yoy_map = _load_yoy_map() if args.yoy_strict else {}
    if args.yoy_strict:
        print(f'业绩过滤已启用: {len(yoy_map)} 只 (--yoy-strict)')
        # 业绩过滤只能用当前快照, 仅对 2024+ 启用 (避免历史业绩变化误判)
        YOY_HISTORY_START = '20240101'
        print(f'业绩过滤窗口: {YOY_HISTORY_START}~ (避免历史快照失真)\n')
    else:
        YOY_HISTORY_START = None
        print('业绩过滤关闭 (默认)\n')

    # 跑每个阈值
    summary = []
    trade_db = {t: [] for t in thresholds}
    for thr in thresholds:
        for code, (closes, dates, rsi) in code_rsi.items():
            # 2026-09-22 加: ST 票预筛 (单票过滤一次)
            if code in st_codes:
                continue
            # 2026-09-22 加: 业绩过滤预查 (只算一次 / 只)
            yoy = yoy_map.get(code) if args.yoy_strict else None
            for i in range(30, len(closes) - args.hold_days):
                if np.isnan(rsi[i]) or rsi[i] >= thr: continue
                # 2026-09-22 加: 业绩过滤 (4 重反向排除)
                if args.yoy_strict:
                    # 仅对 2024+ 信号启用业绩过滤
                    if dates[i] < YOY_HISTORY_START:
                        continue
                    if not _passes_yoy_filter(yoy):
                        continue
                buy = closes[i + 1] if i + 1 < len(closes) else closes[i]
                sell_idx = i + args.hold_days
                if sell_idx >= len(closes): break
                ret = (closes[sell_idx] - buy) / buy * 100
                future_high = max(closes[i+1:sell_idx+1])
                future_low = min(closes[i+1:sell_idx+1])
                max_gain = (future_high - buy) / buy * 100
                max_dd = (future_low - buy) / buy * 100
                trade_db[thr].append({
                    'code': code, 'date': dates[i], 'rsi6': rsi[i],
                    'thr': thr,
                    'industry': industry_map.get(code, ''),
                    'buy': buy, 'sell': closes[sell_idx],
                    'max_gain': max_gain, 'max_dd': max_dd, 'ret': ret,
                })
        n = len(trade_db[thr])
        if n == 0:
            summary.append({'thr': thr, 'n': 0})
            continue
        wins = sum(1 for tr in trade_db[thr] if tr['max_gain'] >= 5)
        losses = sum(1 for tr in trade_db[thr] if tr['max_dd'] <= -5)
        total = wins + losses
        wr5 = wins/total*100 if total else 0
        expected = (wins*5 + losses*-5) / n
        avg_ret = sum(tr['ret'] for tr in trade_db[thr]) / n
        wins3 = sum(1 for tr in trade_db[thr] if tr['ret'] > 0)
        wr_pos = wins3/n*100
        summary.append({
            'thr': thr, 'n': n,
            'wr5': wr5, 'wr_pos': wr_pos,
            'expected': expected, 'avg_ret': avg_ret,
            'wins5': wins, 'losses5': losses, 'total5': total,
        })

    # 输出
    print(f"{'阈值':<8}{'笔数':<8}{'5%/5% 胜率':<14}{'持股胜率':<12}{'期望':<10}{'平均终收益':<10}")
    print('-' * 70)
    for s in summary:
        if s['n'] == 0:
            print(f"RSI6<{s['thr']:<6}{0:<8}{'—':<14}{'—':<12}{'—':<10}{'—':<10}")
        else:
            print(f"RSI6<{s['thr']:<6}{s['n']:<8}{s['wr5']:<14.1f}{s['wr_pos']:<12.1f}{s['expected']:<+10.2f}{s['avg_ret']:<+10.2f}")

    # 推荐阈值 (max expected) 写入文件
    if args.write_md:
        valid = [s for s in summary if s['n'] > 0]
        if valid:
            best = max(valid, key=lambda s: s['expected'])
            out_path = ROOT / 'docs' / 'rsi6-backtest-trades.md'
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(f'# RSI6 超卖反弹回测 (持有 {args.hold_days} 天)\n\n')
                f.write(f'> 推荐阈值: **RSI6 < {best["thr"]}** (单笔期望 {best["expected"]:+.2f}%)\n\n')
                f.write(f'> 业绩过滤: {"启用" if args.yoy_strict else "关闭"}, '
                        f'时间窗口: 2024+ (仅启用业绩过滤时)\n\n' if args.yoy_strict else '\n')
                f.write('## 阈值对比\n\n')
                f.write('| 阈值 | 笔数 | 5%/5% 胜率 | 持股胜率 | 单笔期望 | 平均终 |\n')
                f.write('|---|---|---|---|---|---|\n')
                for s in summary:
                    if s['n'] == 0:
                        f.write(f"| RSI6<{s['thr']} | 0 | — | — | — | — |\n")
                    else:
                        f.write(f"| RSI6<{s['thr']} | {s['n']} | {s['wr5']:.1f}% | {s['wr_pos']:.1f}% | {s['expected']:+.2f}% | {s['avg_ret']:+.2f}% |\n")
                # 推荐阈值明细 (前 200 笔)
                best_trades = trade_db[best['thr']]
                f.write(f'\n## RSI6<{best["thr"]} 明细 (前 200 / 总 {len(best_trades)})\n\n')
                f.write('| 代码 | 触发日 | RSI6 | 买入价 | max_gain | max_dd | 持有终 |\n')
                f.write('|---|---|---|---|---|---|---|\n')
                for t in best_trades[:200]:
                    f.write(f"| {t['code']} | {t['date']} | {t['rsi6']:.1f} | ¥{t['buy']:.2f} | "
                            f"{t['max_gain']:+.2f}% | {t['max_dd']:+.2f}% | {t['ret']:+.2f}% |\n")
                f.write(f'\n(总 {len(best_trades)} 笔, 仅显示前 200)\n')
            print(f'\n📄 {out_path}')

    # 2026-09-22 加: --write-trades 写所有 trade 明细到 parquet
    if args.write_trades:
        all_trades = []
        for thr, trades in trade_db.items():
            all_trades.extend(trades)
        if all_trades:
            trades_df = pd.DataFrame(all_trades)
            out_trades = ROOT / 'data' / 'rsi6_backtest_trades.parquet'
            out_trades.parent.mkdir(parents=True, exist_ok=True)
            trades_df.to_parquet(out_trades, index=False)
            print(f'\n📊 {len(all_trades):,} trades → {out_trades}')


if __name__ == '__main__':
    main()
