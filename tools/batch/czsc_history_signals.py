"""
tools/batch/czsc_history_signals.py — 给单只股票算 czsc 17 个信号的历史触发

不集成到因子历史表, 只输出一份 markdown 报告, 验证 czsc_signals 在历史到底准不准.

用法:
  bash tools/with_venv.sh python -m tools.batch.czsc_history_signals --code 002475
  ... --code 002475 --lookback 400
"""
import argparse
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _label_for(sig_name: str, sig_val: str, label_map: dict) -> str:
    """czsc sig_name + sig_val → 中文标签 (复用 czsc_signals.py 逻辑)"""
    entry = label_map.get(sig_name)
    if entry is None:
        # 从 sig_val 推方向
        if any(t in sig_val for t in ("看多", "看涨", "底背", "买入", "趋势跟随")):
            return "🟢"
        if any(t in sig_val for t in ("看空", "看跌", "顶背", "卖出")):
            return "🔴"
        return ""
    label, _dir = entry
    return label


def main():
    parser = argparse.ArgumentParser(description="czsc 17 信号历史触发 (单只验证用)")
    parser.add_argument("--code", required=True, help="股票代码")
    parser.add_argument("--lookback", type=int, default=400, help="回看 K 线条数")
    parser.add_argument("--output", type=str, default="", help="输出 md 路径 (空=打印)")
    args = parser.parse_args()

    from tools.storage.store import DataStore
    from tools.factors.chan.czsc_signals import (
        SIGNAL_TEMPLATES, CS_KEY_LABEL_MAP, _VAL_BUY_TOKENS, _VAL_SELL_TOKENS
    )

    # 1. 加载 K 线
    with redirect_stdout(io.StringIO()):
        ctx = DataStore.get_ctx(args.code, kline_only=True, limit=args.lookback)
    if not ctx.kline or len(ctx.kline) < 60:
        print(f"K线不足 60 根 ({len(ctx.kline)})")
        return
    klines = ctx.kline
    dates = [k["trade_date"][:10] for k in klines]
    closes = [k["close"] for k in klines]
    print(f"📊 {args.code} K线: {len(klines)} 根 ({dates[0]} ~ {dates[-1]}) 最新 ¥{closes[-1]}")

    # 2. 转 czsc RawBar
    from tools.factors.chan.czsc_wrapper import klines_to_raw_bars
    import czsc as _czsc
    bars = klines_to_raw_bars([{
        "date": k["trade_date"],
        "open": k["open"],
        "close": k["close"],
        "high": k["high"],
        "low": k["low"],
        "vol": k.get("vol", 0) or 0,
    } for k in klines], args.code)

    # 3. 配置 17 个信号 + 增量跑
    cfg = _czsc.get_signals_config(list(SIGNAL_TEMPLATES.values()))
    bg = _czsc.BarGenerator(bars[0].freq, [], 1000)
    cs = _czsc.CzscSignals(bg, cfg)

    # 记录每次 update 后的快照
    history = []
    for i, bar in enumerate(bars):
        cs.update_signals(bar)
        snap = cs.get_signals_by_conf()  # 当前 bar 的 signals dict
        snap["_date"] = str(bar.dt)[:10] if hasattr(bar, 'dt') else dates[i]
        snap["_close"] = closes[i]
        history.append(snap)

    print(f"📊 czsc update 快照: {len(history)} 个")

    # 4. 统计 17 个信号每次的触发 (跳过 '其他_*') — 用 CS_KEY_LABEL_MAP key (无中文, 跟 czsc 实际生成的对齐)
    cs_keys = list(CS_KEY_LABEL_MAP.keys())  # 17 个实际 czsc key (无中文)
    triggers = {ck: [] for ck in cs_keys}
    for snap in history:
        for ck in cs_keys:
            val = snap.get(ck, "")
            if not val or val.startswith("其他"):
                continue
            triggers[ck].append({
                "date": snap["_date"],
                "close": snap["_close"],
                "val": val,
            })

    # 5. 输出 markdown
    total_days = len(history)
    md = []
    md.append(f"# czsc 17 信号历史触发 — {args.code}\n\n")
    md.append(f"> 范围: {dates[0]} ~ {dates[-1]} ({total_days} 个交易日, 最新 ¥{closes[-1]})\n\n")
    md.append(f"> 来源: czsc.CzscSignals (跟 ChanStrategy 用同一套, 不是 generate_czsc_signals)\n\n")

    # 用无中文 key (跟 czsc 实际生成的 key 对齐)
    cs_keys = list(CS_KEY_LABEL_MAP.keys())  # 17 个实际 czsc key (无中文)
    triggers = {ck: [] for ck in cs_keys}
    for snap in history:
        for ck in cs_keys:
            val = snap.get(ck, "")
            if not val or val.startswith("其他"):
                continue
            triggers[ck].append({
                "date": snap["_date"],
                "close": snap["_close"],
                "val": val,
            })

    md.append("| czsc key | 中文标签 | 触发次数 | 频率 (天/次) | 首次 | 末次 |\n")
    md.append("|---|---|---|---|---|---|\n")
    for ck in cs_keys:
        trigs = triggers[ck]
        cnt = len(trigs)
        avg = f"{total_days/cnt:.0f}" if cnt else "—"
        first = trigs[0]['date'] if trigs else "—"
        last = trigs[-1]['date'] if trigs else "—"
        # label
        entry = CS_KEY_LABEL_MAP.get(ck)
        label = entry[0] if entry else "(none)"
        md.append(f"| {ck} | {label} | {cnt} | {avg} | {first} | {last} |\n")

    md.append("\n## 各信号触发详情\n\n")
    for ck in cs_keys:
        entry = CS_KEY_LABEL_MAP.get(ck)
        label = entry[0] if entry else "(none)"
        trigs = triggers[ck]
        if not trigs:
            md.append(f"### {label} ({ck}) — **0 次**\n\n")
            continue
        md.append(f"### {label} ({ck}) — {len(trigs)} 次\n\n")
        md.append("| 日期 | 收盘 | sig_val |\n|---|---|---|\n")
        for t in trigs:
            md.append(f"| {t['date']} | ¥{t['close']:.2f} | {t['val']} |\n")
        md.append("\n")

    out = "".join(md)
    if args.output:
        Path(args.output).write_text(out, encoding='utf-8')
        print(f"\n📄 {args.output}")
    else:
        print("\n" + out)


if __name__ == "__main__":
    main()