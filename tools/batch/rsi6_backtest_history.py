"""
rsi6_backtest_history.py — RSI 双指标超卖 历史回测

对历史每一天的 K 线, 判断 RSI6<25 且 RSI12<30 (lookback 2 根任一跌破),
记录每次触发日期 + 股票代码, 然后跟踪未来 N 天表现 (max 涨幅).

用法:
  bash tools/with_venv.sh python -m tools.batch.rsi6_backtest_history
    # 默认全市场, 30/60/120 天持仓
  ... --from-watchlist                  # 仅 watchlist
  ... --hold-days 30                    # 单持仓期
  ... --threshold-rsi6 20               # 紧一点
  ... --lookback 5                      # 看最近 5 根任一跌破
  ... --max-hold 250                    # 每天只允许 1 只持有 250 天 (全市场)

数据:
  - K 线: 5+ 年 (~1200 天), 5800+ 票
  - v4.5 规则: RSI6<25 AND RSI12<30, 最近 lookback 根任一
  - 跟踪未来 30/60/120 个交易日最大涨幅
  - 大盘对比: 沪深 300 (000300.SH) 同期

设计:
  - 每个交易日, 跑全市场 5800 只, 找 RSI 双超卖命中
  - 命中后, 30 天后取 max(high)/entry - 1
  - 默认同一天可能有 N 只命中, 每只都独立跟踪
  - 不做业绩过滤 (RSI skill 的核心是"技术面抄底", 不看业绩)
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.storage.store import DataStore  # noqa: E402
from tools.batch.rsi6_tech_scan import _rsi_arr  # noqa: E402


def precompute_rsi6_rsi12(closes: list[float]) -> tuple[list[float], list[float]]:
    """一次性算 RSI6 + RSI12, 返回等长 lists."""
    return _rsi_arr(closes, 6), _rsi_arr(closes, 12)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold-days", type=int, default=30, help="持仓天数 (默认 30)")
    parser.add_argument("--threshold-rsi6", type=float, default=25.0)
    parser.add_argument("--threshold-rsi12", type=float, default=30.0)
    parser.add_argument("--lookback", type=int, default=2)
    parser.add_argument("--from-watchlist", action="store_true", help="仅 watchlist (默认全市场)")
    parser.add_argument("--max-signals-per-day", type=int, default=3, help="单日最多 3 触发 (过滤低质量)")
    parser.add_argument("--start-date", default="2021-01-01", help="回测起始日期")
    parser.add_argument("--end-date", default="2026-06-30", help="回测截止日期")
    parser.add_argument("--write-md", action="store_true")
    args = parser.parse_args()

    print(f"=== RSI 双指标超卖 历史回测 ({args.start_date} ~ {args.end_date}) ===")
    print(f"RSI6<{args.threshold_rsi6} AND RSI12<{args.threshold_rsi12}, lookback {args.lookback}")
    print(f"持仓 {args.hold_days} 天 | 单日 max {args.max_signals_per_day} 触发")
    print()

    # 1. 加载 K 线 + watchlist
    t0 = time.time()
    print("⏳ 加载全市场 K 线 (~10s)...")
    ks_dict = DataStore.load_all_kline(years=8)

    # 转成 dict[code_with_suffix -> DataFrame]
    kline_df = {}
    for code, bars in ks_dict.items():
        if not bars:
            continue
        df_bars = pd.DataFrame(bars)
        df_bars['trade_date'] = pd.to_datetime(df_bars['trade_date'])
        df_bars['close'] = df_bars['close'].astype(float)
        kline_df[code] = df_bars.sort_values('trade_date').reset_index(drop=True)

    # watchlist 过滤
    if args.from_watchlist:
        with open('config/watchlist.json') as f:
            import json
            wl = json.load(f)
        wl_codes = {s['code'] for s in wl['stocks']}
        # 映射到带后缀的 key
        wl_with_suffix = {c: next((k for k in kline_df.keys() if k.startswith(c + '.')), None) for c in wl_codes}
        wl_with_suffix = {k: v for k, v in wl_with_suffix.items() if v is not None}
        kline_df = {k: v for k, v in kline_df.items() if k.split('.')[0] in wl_codes}
        print(f"⏳ watchlist 过滤后剩 {len(kline_df)} 只")

    print(f"⏳ 加载完成 ({time.time()-t0:.1f}s): {len(kline_df)} 只 K 线\n")

    # 2. 对每只每天扫描
    print(f"⏳ 扫描 RSI 双指标超卖 (每日 全市场 {len(kline_df)} 只)...")
    t_scan = time.time()

    # 索引化每只的 date -> (rsi6, rsi12) dict
    # 起始/截止日期约束
    start_dt = pd.Timestamp(args.start_date)
    end_dt = pd.Timestamp(args.end_date)
    hold_days = args.hold_days

    triggers = []  # [(trigger_date, code_short, name, rsi6, rsi12, entry_close), ...]

    for code_suffix, df in kline_df.items():
        code_short = code_suffix.split('.')[0]
        closes = df['close'].tolist()
        rsi6_arr, rsi12_arr = precompute_rsi6_rsi12(closes)

        for i in range(args.lookback, len(df)):
            # 取最近 lookback 根任一跌破
            recent6 = [rsi6_arr[i - k] for k in range(args.lookback)]
            recent12 = [rsi12_arr[i - k] for k in range(args.lookback)]

            # 任一 RSI6<25 且 同一根 RSI12<30
            triggered = False
            for k in range(args.lookback):
                r6 = recent6[k]
                r12 = recent12[k]
                if not pd.isna(r6) and not pd.isna(r12) and r6 < args.threshold_rsi6 and r12 < args.threshold_rsi12:
                    triggered = True
                    break
            if not triggered:
                continue

            t = df.iloc[i]['trade_date']
            if t < start_dt or t > end_dt:
                continue
            triggers.append({
                    'trigger_date': t,
                    'code': code_short,
                    'rsi6': rsi6_arr[i],
                    'rsi12': rsi12_arr[i],
                    'entry_close': closes[i],
                })

    print(f"⏳ 扫描完成 ({time.time()-t_scan:.1f}s): {len(triggers)} 次原始触发")
    if not triggers:
        print("无任何触发")
        return

    # 3. 单日限 N 触发 (按 RSI6 升序取最弱的 N 个)
    triggers.sort(key=lambda x: (x['trigger_date'], x['rsi6']))
    by_date = {}
    for t in triggers:
        by_date.setdefault(t['trigger_date'], []).append(t)
    final_triggers = []
    for d, lst in by_date.items():
        lst.sort(key=lambda x: x['rsi6'])
        final_triggers.extend(lst[:args.max_signals_per_day])
    print(f"⏳ 单日限 {args.max_signals_per_day} 触发后: {len(final_triggers)} 次\n")

    # 4. 跟踪每只未来 N 天 max 涨幅
    print(f"⏳ 跟踪未来 {hold_days} 天表现...")
    returns = []  # list of (trigger_date, code, rsi6, rsi12, return_pct, entry, exit_max)

    for t in final_triggers:
        code_short = t['code']
        # 找带后缀的 key
        code_key = next((k for k in kline_df.keys() if k.startswith(code_short + '.')), None)
        if code_key is None:
            continue
        df = kline_df[code_key]
        # 找 entry 之后的 N 天
        future = df[df['trade_date'] > t['trigger_date']].head(hold_days)
        if len(future) < 2:
            continue
        entry_close = t['entry_close']
        max_high = future['high'].max()
        ret = (max_high - entry_close) / entry_close * 100
        returns.append({
            'trigger_date': t['trigger_date'],
            'code': code_short,
            'rsi6': t['rsi6'],
            'rsi12': t['rsi12'],
            'entry': entry_close,
            'max_high': max_high,
            'return': ret,
            'exit_date': future.iloc[-1]['trade_date'],
        })

    if not returns:
        print("无完整跟踪结果")
        return

    print(f"⏳ 跟踪完成: {len(returns)} 只次\n")

    # 5. 统计
    rets = [r['return'] for r in returns]
    win = sum(1 for r in rets if r > 0)
    win_pct = win / len(rets) * 100
    avg = sum(rets) / len(rets)
    median = sorted(rets)[len(rets)//2]
    max_r = max(rets)
    min_r = min(rets)
    top10 = sorted(rets, reverse=True)[:max(1, len(rets)//10)]

    print(f"=== RSI 双指标超卖统计 (持仓 {hold_days} 天) ===")
    print(f"  样本: {len(rets)} 次触发")
    print(f"  胜率 (> 0%): {win} / {len(rets)} = {win_pct:.1f}%")
    print(f"  平均 max 涨幅: {avg:+.1f}%")
    print(f"  中位 max 涨幅: {median:+.1f}%")
    print(f"  最高 max 涨幅: {max_r:+.1f}%")
    print(f"  最低 max 涨幅: {min_r:+.1f}%")
    print(f"  头部 10% 平均: {sum(top10)/len(top10):+.1f}%")

    # 6. 大盘对比 (沪深 300 同期)
    hs300_df = kline_df.get('000300.SH')
    hs300_benchmarks = []
    if hs300_df is not None:
        # 采样: 每 30 天一个观察点 (避免过多)
        for r in returns[:50]:
            future = hs300_df[hs300_df['trade_date'] >= r['trigger_date']].head(hold_days + 1)
            if len(future) < 2:
                continue
            entry_close = future.iloc[0]['close']
            max_high = future['high'].max()
            ret = (max_high - entry_close) / entry_close * 100
            hs300_benchmarks.append({'date': r['trigger_date'], 'return': ret})

        if hs300_benchmarks:
            avg_b = sum(b['return'] for b in hs300_benchmarks) / len(hs300_benchmarks)
            print(f"\n=== 沪深 300 同期 (持仓 {hold_days} 天) ===")
            print(f"  样本: {len(hs300_benchmarks)}")
            print(f"  平均 max 涨幅: {avg_b:+.1f}%")

            alpha = avg - avg_b
            print(f"\n=== Alpha 对比 ===")
            print(f"  RSI 双超卖 平均:    {avg:+.1f}%")
            print(f"  沪深 300 平均:      {avg_b:+.1f}%")
            print(f"  Alpha:               {alpha:+.1f}% pp")
            if alpha > 5:
                print(f"  ✅ 显著跑赢大盘")
            elif alpha > 0:
                print(f"  ⚠️ 小幅跑赢大盘")
            else:
                print(f"  ❌ 跑输大盘")

    # 7. 按年份拆分
    by_year = {}
    for r in returns:
        y = r['trigger_date'].year
        by_year.setdefault(y, []).append(r['return'])

    print(f"\n=== 按年份拆分 ===")
    print(f"  年份 | 触发数 | 平均 max 涨幅")
    for y in sorted(by_year.keys()):
        rs = by_year[y]
        avg_y = sum(rs) / len(rs)
        win_y = sum(1 for r in rs if r > 0) / len(rs) * 100
        print(f"  {y}  | {len(rs):>5}   | 平均 {avg_y:+.1f}% | 胜率 {win_y:.1f}%")

    # 8. 写报告
    if args.write_md:
        import datetime
        out_path = ROOT / 'docs' / 'rsi6-tech-backtest.md'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = [f"# RSI 双指标超卖 历史回测 ({datetime.datetime.now().strftime('%Y-%m-%d')})\n\n"]
        md.append(f"> 测试期: {args.start_date} ~ {args.end_date}\n")
        md.append(f"> 规则: RSI6<{args.threshold_rsi6} AND RSI12<{args.threshold_rsi12}, lookback {args.lookback} 根任一跌破\n")
        md.append(f"> 持仓期: {hold_days} 天\n\n")
        md.append(f"## 统计\n\n")
        md.append(f"| 指标 | 值 |\n|---|---|\n")
        md.append(f"| 样本 | {len(rets)} |\n")
        md.append(f"| 胜率 | {win_pct:.1f}% |\n")
        md.append(f"| 平均 max 涨幅 | {avg:+.1f}% |\n")
        md.append(f"| 中位 max 涨幅 | {median:+.1f}% |\n")
        md.append(f"| 最高 max 涨幅 | {max_r:+.1f}% |\n")
        md.append(f"| 最低 max 涨幅 | {min_r:+.1f}% |\n\n")
        if hs300_benchmarks:
            avg_b = sum(b['return'] for b in hs300_benchmarks) / len(hs300_benchmarks)
            md.append(f"## 沪深 300 对比\n\n")
            md.append(f"- 沪深 300 同期平均: {avg_b:+.1f}%\n")
            md.append(f"- Alpha: {avg - avg_b:+.1f}% pp\n\n")
        md.append(f"## 按年份拆分\n\n")
        md.append(f"| 年份 | 触发数 | 平均涨幅 | 胜率 |\n|---|---|---|---|\n")
        for y in sorted(by_year.keys()):
            rs = by_year[y]
            avg_y = sum(rs) / len(rs)
            win_y = sum(1 for r in rs if r > 0) / len(rs) * 100
            md.append(f"| {y} | {len(rs)} | {avg_y:+.1f}% | {win_y:.1f}% |\n")
        out_path.write_text(''.join(md), encoding='utf-8')
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()