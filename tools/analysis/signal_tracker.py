#!/usr/bin/env python3
"""
Mavis Signal Tracker v1.0 (2026-07-20)

手动工具,用于:
1. 记录新信号 → data/signals/YYYY-MM-DD.jsonl
2. 更新已有信号的 outcome (拉最新股价, 比对 target/stop)
3. 统计胜率 (按 code / signal_type / 时间窗口)

用法:
  # 记录信号 (从 JSON 文件读取)
  python3 signal_tracker.py --record --input signals_to_record.json

  # 交互式记录单条信号
  python3 signal_tracker.py --record-interactive

  # 更新所有 pending 信号的 outcome
  python3 signal_tracker.py --update

  # 更新单个信号
  python3 signal_tracker.py --update --signal-id 688256_2026-07-20_T+1

  # 统计所有信号胜率
  python3 signal_tracker.py --stats

  # 按 code 统计
  python3 signal_tracker.py --stats --code 688256

  # 按 signal_type 统计
  python3 signal_tracker.py --stats --type 底背驰

  # 看 7d 窗口胜率
  python3 signal_tracker.py --stats --window 7
"""

import json
import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_DIR = ROOT / 'data' / 'signals'

# 把项目根加到 sys.path, 便于 'from tools.xxx import' 找到模块
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def fetch_price(code):
    """拉今日价格 (走 tushare_fetcher 直连, 2026-08-26 删 data_source 间接层)"""
    try:
        from tools.storage.sources.tushare import get_daily_basic
        rows, status = get_daily_basic(code, limit=1)
        if status != "OK" or not rows:
            return None
        r = rows[0]
        return {
            'price': float(r.get('close', 0) or 0),
            # Tushare.daily_basic 没给 prev_close/open/high/low/pct, 标 0
            'prev_close': 0, 'open': 0, 'high': 0, 'low': 0, 'pct': 0,
        }
    except Exception as e:
        return None


def fetch_kline_range(code, start_date, end_date):
    """拉 start_date 到 end_date 之间的 K 线 (走 tushare_fetcher, 2026-08-26 删 data_source 间接层)"""
    try:
        from tools.storage.sources.tushare import get_daily
        bars, status = get_daily(code, limit=400)
        if status != "OK" or not bars:
            return []
        result = []
        for b in bars:
            dt = b.get("trade_date", "")
            if start_date <= dt <= end_date:
                result.append({
                    'date': dt,
                    'open': float(b.get('open', 0) or 0),
                    'close': float(b.get('close', 0) or 0),
                    'high': float(b.get('high', 0) or 0),
                    'low': float(b.get('low', 0) or 0),
                })
        return result
    except Exception as e:
        return []


def load_all_signals():
    """加载所有 signals/*.jsonl"""
    signals = []
    if not SIGNALS_DIR.exists():
        return signals
    for f in sorted(SIGNALS_DIR.glob('*.jsonl')):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line: continue
                try:
                    signals.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"⚠️ 解析失败 {f}: {line[:80]}", file=sys.stderr)
    return signals


def save_signals_to_file(signals, date):
    """保存到 data/signals/YYYY-MM-DD.jsonl"""
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    f = SIGNALS_DIR / f'{date}.jsonl'
    with open(f, 'a') as fh:
        for s in signals:
            # 自动补 model 字段 (从 signal_type 推断)
            if 'model' not in s:
                s['model'] = infer_model(s.get('signal_type', ''))
            fh.write(json.dumps(s, ensure_ascii=False) + '\n')
    print(f"✅ 已保存 {len(signals)} 条信号 → {f}")


# signal_type → model 推断 (按子串匹配, 顺序重要)
MODEL_INFERENCE = [
    ('BC+中枢+MA+威科夫', ['BC+中枢+MA+威科夫', '3合1', '3合 1', 'BC_HUB_MA', 'top_alert']),
    ('缠论', ['中枢', '背驰', '止跌', '1买', '2买', '3买', '1卖', '2卖', '3卖', '60分底', '60分顶', '日线底', '日线顶']),
    ('SMC', ['Demand', 'Supply', 'BOS', 'CHoCH', 'OB']),
    ('威科夫', ['威科夫', 'A累积', 'B弹簧', 'C测试', 'D突破', 'E顶部', '阶段A', '阶段B', '阶段C', '阶段D', '阶段E']),
    ('PEG', ['PEG_', 'L_可达', 'L/可达', 'PEG<', 'PEG>']),
    ('fflow', ['fflow_', '主力_', '主力净', '5日净', '进货', '出货']),
    ('T框架', ['T+', 'T-', 'T_']),
    ('板块', ['板块', 'sector']),
]


def infer_model(signal_type):
    """从 signal_type 推断 model, 找不到返回 '其他'"""
    for model, keywords in MODEL_INFERENCE:
        for kw in keywords:
            if kw in signal_type:
                return model
    return '其他'


def record_signals(signals_list):
    """记录信号到对应日期文件"""
    by_date = {}
    for s in signals_list:
        date = s.get('date', datetime.now().strftime('%Y-%m-%d'))
        by_date.setdefault(date, []).append(s)
    for date, sigs in by_date.items():
        save_signals_to_file(sigs, date)


def update_signal_outcome(s):
    """更新单条信号的 outcome"""
    if s.get('outcome') in ('hit_target', 'hit_stop', 'expired'):
        return s  # 已确定, 不更新

    code = s['code']
    signal_date = s['date']
    hold_days = s.get('expected_hold_days', 30)
    target = s.get('target_price', 0)
    stop = s.get('stop_loss', 0)
    trigger = s.get('trigger_price', 0)
    direction = s.get('expected_direction', 'long')

    # 决定 end_date
    signal_dt = datetime.strptime(signal_date, '%Y-%m-%d')
    end_dt = signal_dt + timedelta(days=hold_days)
    today = datetime.now()
    end_date = min(end_dt, today).strftime('%Y-%m-%d')

    # 拉 K 线
    klines = fetch_kline_range(code, signal_date, end_date)
    if not klines:
        print(f"  ⚠️ {s['signal_id']} 无法拉 K 线")
        return s

    # 在 hold 期内检查
    for k in klines:
        if direction == 'long':
            if target and k['high'] >= target:
                s['outcome'] = 'hit_target'
                s['outcome_date'] = k['date']
                s['outcome_price'] = target
                s['outcome_pnl_pct'] = round((target / trigger - 1) * 100, 2)
                return s
            if stop and k['low'] <= stop:
                s['outcome'] = 'hit_stop'
                s['outcome_date'] = k['date']
                s['outcome_price'] = stop
                s['outcome_pnl_pct'] = round((stop / trigger - 1) * 100, 2)
                return s
        else:  # short
            if target and k['low'] <= target:
                s['outcome'] = 'hit_target'
                s['outcome_date'] = k['date']
                s['outcome_price'] = target
                s['outcome_pnl_pct'] = round((trigger / target - 1) * 100, 2)
                return s
            if stop and k['high'] >= stop:
                s['outcome'] = 'hit_stop'
                s['outcome_date'] = k['date']
                s['outcome_price'] = stop
                s['outcome_pnl_pct'] = round((trigger / stop - 1) * 100, 2)
                return s

    # 检查是否已过期
    if today >= end_dt:
        last_close = klines[-1]['close'] if klines else trigger
        s['outcome'] = 'expired'
        s['outcome_date'] = klines[-1]['date'] if klines else signal_date
        s['outcome_price'] = last_close
        s['outcome_pnl_pct'] = round((last_close / trigger - 1) * 100, 2) if direction == 'long' else round((trigger / last_close - 1) * 100, 2)
    # else: still pending

    return s


def save_updated_signals(signals):
    """重写所有 signals 到对应文件 (因为 outcome 更新了)"""
    by_date = {}
    for s in signals:
        # 自动补 model 字段 (旧数据迁移)
        if 'model' not in s:
            s['model'] = infer_model(s.get('signal_type', ''))
        date = s['date']
        by_date.setdefault(date, []).append(s)

    for date, sigs in by_date.items():
        f = SIGNALS_DIR / f'{date}.jsonl'
        with open(f, 'w') as fh:
            for s in sigs:
                fh.write(json.dumps(s, ensure_ascii=False) + '\n')
    print(f"✅ 已重写 {len(signals)} 条信号到 {len(by_date)} 个文件")


def cmd_update(args):
    signals = load_all_signals()
    if args.signal_id:
        signals = [s for s in signals if s.get('signal_id') == args.signal_id]
        if not signals:
            print(f"❌ 没找到 signal_id={args.signal_id}")
            return
    print(f"🔄 更新 {len(signals)} 条信号 outcome...")
    updated = [update_signal_outcome(s) for s in signals]
    save_updated_signals(updated)
    # 摘要
    outcomes = {}
    for s in updated:
        outcomes[s.get('outcome', 'pending')] = outcomes.get(s.get('outcome', 'pending'), 0) + 1
    print(f"\n📊 更新后状态分布: {outcomes}")


def cmd_record(args):
    if args.input:
        with open(args.input) as f:
            signals = json.load(f)
        record_signals(signals)
    elif args.interactive:
        print("📝 交互式记录 (单条):")
        sig = {
            'signal_id': input("signal_id: ").strip(),
            'code': input("code: ").strip(),
            'name': input("name: ").strip(),
            'date': input("date (YYYY-MM-DD, 默认今天): ").strip() or datetime.now().strftime('%Y-%m-%d'),
            'signal_type': input("signal_type (T+1/PEG_buy/底背驰/...): ").strip(),
            'source': input("source: ").strip(),
            'trigger_price': float(input("trigger_price: ")),
            'target_price': float(input("target_price: ")),
            'stop_loss': float(input("stop_loss: ")),
            'expected_direction': input("direction (long/short): ").strip() or 'long',
            'expected_hold_days': int(input("hold_days (默认30): ").strip() or '30'),
            'confidence': input("confidence (high/medium/low): ").strip() or 'medium',
            'rationale': input("rationale: ").strip(),
        }
        sig['key_signals'] = {}
        sig['outcome'] = 'pending'
        record_signals([sig])
    else:
        print("❌ 需要 --input 或 --interactive")


def cmd_stats(args):
    signals = load_all_signals()
    # 自动补 model 字段 (旧数据迁移)
    for s in signals:
        if 'model' not in s:
            s['model'] = infer_model(s.get('signal_type', ''))

    if args.code:
        signals = [s for s in signals if s['code'] == args.code]
    if args.type:
        signals = [s for s in signals if s.get('signal_type') == args.type]
    if args.model:
        signals = [s for s in signals if s.get('model') == args.model]

    # 窗口过滤
    if args.window:
        cutoff = (datetime.now() - timedelta(days=args.window)).strftime('%Y-%m-%d')
        signals = [s for s in signals if s['date'] >= cutoff]

    # 排除 pending
    finished = [s for s in signals if s.get('outcome') in ('hit_target', 'hit_stop', 'expired')]

    print(f"\n{'='*70}")
    print(f"📊 信号胜率统计 (窗口: {args.window or 'all'}d, 总数: {len(signals)})")
    print(f"{'='*70}")
    print(f"已完成: {len(finished)} 条, Pending: {len(signals) - len(finished)} 条\n")

    if not finished:
        print("⚠️ 暂无已完成的信号")
        # 仍然按 model 显示总信号数 (含 pending)
        by_model_all = {}
        for s in signals:
            m = s.get('model', '其他')
            by_model_all.setdefault(m, []).append(s)
        if by_model_all:
            print(f"\n📊 按 model 分组 (全部 {len(signals)} 条):")
            for m, sigs in sorted(by_model_all.items(), key=lambda x: -len(x[1])):
                pending = sum(1 for s in sigs if s.get('outcome') == 'pending')
                done = len(sigs) - pending
                print(f"  {m:<10} 总数={len(sigs):<3} 已完成={done}  Pending={pending}")
        return

    # 总体
    target_count = sum(1 for s in finished if s['outcome'] == 'hit_target')
    stop_count = sum(1 for s in finished if s['outcome'] == 'hit_stop')
    expired_count = sum(1 for s in finished if s['outcome'] == 'expired')

    win_rate = target_count / len(finished) * 100
    avg_pnl = sum(s.get('outcome_pnl_pct', 0) for s in finished) / len(finished)
    target_pnl = sum(s.get('outcome_pnl_pct', 0) for s in finished if s['outcome'] == 'hit_target') / max(target_count, 1)
    stop_pnl = sum(s.get('outcome_pnl_pct', 0) for s in finished if s['outcome'] == 'hit_stop') / max(stop_count, 1)

    print(f"🎯 总体:")
    print(f"  胜率 (hit_target/all): {win_rate:.1f}%  ({target_count}/{len(finished)})")
    print(f"  平均 PnL: {avg_pnl:+.2f}%")
    print(f"  hit_target 平均 PnL: {target_pnl:+.2f}%")
    print(f"  hit_stop 平均 PnL: {stop_pnl:+.2f}%")
    print(f"  expired: {expired_count} 条")

    # 按 model 分组 — 全部信号 (含 pending) — 永远显示
    by_model_all = {}
    for s in signals:
        m = s.get('model', '其他')
        by_model_all.setdefault(m, []).append(s)
    print(f"\n📊 按 model 分组 (全部 {len(signals)} 条, 含 pending):")
    for m, sigs in sorted(by_model_all.items(), key=lambda x: -len(x[1])):
        pending = sum(1 for s in sigs if s.get('outcome') == 'pending')
        done = len(sigs) - pending
        target_done = sum(1 for s in sigs if s.get('outcome') == 'hit_target')
        stop_done = sum(1 for s in sigs if s.get('outcome') == 'hit_stop')
        expired_done = sum(1 for s in sigs if s.get('outcome') == 'expired')
        pnl_vals = [s.get('outcome_pnl_pct', 0) for s in sigs if s.get('outcome_pnl_pct') is not None]
        avg = sum(pnl_vals)/len(pnl_vals) if pnl_vals else 0
        rate_str = f"{target_done}/{done}" if done > 0 else "—"
        print(f"  {m:<10} 总数={len(sigs):<3} 已完成={done} (hit/target={target_done} hit/stop={stop_done} expired={expired_done}) Pending={pending}  胜率={rate_str}  平均PnL={avg:+.2f}%")

    # 按 model 分组 — 已完成胜率排名 (5 大类模型排名) 🆕
    by_model = {}
    for s in finished:
        m = s.get('model', '其他')
        by_model.setdefault(m, []).append(s)
    if len(by_model) > 1:
        print(f"\n📊 按 model 分组 (5 大类模型):")
        for m, sigs in sorted(by_model.items(), key=lambda x: -len(x[1])):
            tgt = sum(1 for s in sigs if s['outcome'] == 'hit_target')
            rate = tgt / len(sigs) * 100
            avg = sum(s.get('outcome_pnl_pct', 0) for s in sigs) / len(sigs)
            star = '⭐' if rate >= 60 else '🟡' if rate >= 50 else '❌'
            print(f"  {m:<10} 样本={len(sigs):<3} 胜率={rate:.0f}% {star}  平均PnL={avg:+.2f}%")

    # 按 signal_type 分组
    by_type = {}
    for s in finished:
        t = s.get('signal_type', 'unknown')
        by_type.setdefault(t, []).append(s)
    if len(by_type) > 1:
        print(f"\n📊 按 signal_type 分组:")
        for t, sigs in sorted(by_type.items(), key=lambda x: -len(x[1])):
            tgt = sum(1 for s in sigs if s['outcome'] == 'hit_target')
            rate = tgt / len(sigs) * 100
            avg = sum(s.get('outcome_pnl_pct', 0) for s in sigs) / len(sigs)
            print(f"  {t:<20} 样本={len(sigs):<3} 胜率={rate:.0f}%  平均PnL={avg:+.2f}%")

    # 按 code 分组
    by_code = {}
    for s in finished:
        c = s['code']
        by_code.setdefault(c, []).append(s)
    if len(by_code) > 1:
        print(f"\n📊 按 code 分组:")
        for c, sigs in sorted(by_code.items(), key=lambda x: -len(x[1])):
            tgt = sum(1 for s in sigs if s['outcome'] == 'hit_target')
            rate = tgt / len(sigs) * 100
            avg = sum(s.get('outcome_pnl_pct', 0) for s in sigs) / len(sigs)
            print(f"  {c} 样本={len(sigs):<3} 胜率={rate:.0f}%  平均PnL={avg:+.2f}%")

    print()


def main():
    parser = argparse.ArgumentParser(description='Mavis Signal Tracker v1.0')
    sub = parser.add_subparsers(dest='cmd')

    # record
    p_rec = sub.add_parser('record', help='记录新信号')
    p_rec.add_argument('--input', help='JSON 文件路径 (数组格式)')
    p_rec.add_argument('--interactive', action='store_true', help='交互式记录')
    p_rec.set_defaults(func=cmd_record)

    # update
    p_upd = sub.add_parser('update', help='更新 pending 信号的 outcome')
    p_upd.add_argument('--signal-id', help='只更新指定 signal_id')
    p_upd.set_defaults(func=cmd_update)

    # stats
    p_st = sub.add_parser('stats', help='统计信号胜率')
    p_st.add_argument('--code', help='按 code 过滤')
    p_st.add_argument('--type', help='按 signal_type 过滤')
    p_st.add_argument('--model', help='按 model (BC+中枢+MA+威科夫/缠论/SMC/威科夫/PEG/fflow/T框架/板块) 过滤')
    p_st.add_argument('--window', type=int, help='只统计最近 N 天')
    p_st.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return
    args.func(args)


if __name__ == '__main__':
    main()
