"""
batch_summary.py — 批量扫描全 watchlist, 输出有 代码+名称+场景+信号 的 batch md

关键变化 (v2.0+):
- 不调 RenderData.from_raw, 直接读 docs/{portfolio,watchlist}/analyze-{code}-{name}.md
- 从 "## 📈 因子历史走势" section 提取最后一行 (12 列) → batch md
- 10 天合集: 调 analyze_history 预算 history, 再传 compute_factor_history(ctx, history=history)
  (复用, 跟 t_analyze_all 一致, 不重算)
- 默认输出 `docs/signal-watchlist.md` (单文件, 每天覆盖), `--out` 可自定义
- 0 重算, 0 from_raw

**用法**:
  python -m tools.batch.batch_summary              # 默认 watchlist 全部 → signal-watchlist.md
  python -m tools.batch.batch_summary 300274 600089  # 指定 codes
  python -m tools.batch.batch_summary --sector CPO  # 板块筛选
  python -m tools.batch.batch_summary --out /tmp/x.md

**对应**: refresh_all.sh 末尾自动调这个生成 signal-watchlist.md
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
WATCHLIST_JSON = PROJECT_ROOT / "data" / "watchlist.json"
DOCS_DIR = PROJECT_ROOT / "docs"


def _load_watchlist() -> list[dict]:
    return json.load(open(WATCHLIST_JSON))["stocks"]


def _filter_by_sector(stocks: list[dict], sector: str) -> list[dict]:
    out = []
    for s in stocks:
        industry = s.get("industry", "")
        note = s.get("note", "")
        tags = s.get("tags", [])
        if sector in industry or sector in note or sector in tags:
            out.append(s)
    return out


def _parse_md_last_row(md_path: Path) -> dict | None:
    """从 docs/analyze-{code}-{name}.md 读 "## 📈 因子历史走势" section 最后一行
    当前 header (report_renderer.py:1463):
      日期|收盘|威科夫(日/周)|子事件(日/周)|MA(日/周)|日中枢|周中枢|买卖点|变化|日分(顶/底)|A天(日/周)|OBV|布林%|MA120偏离
    返 None 表示文件不存在或 section 没数据
    """
    if not md_path.exists():
        return None
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return None

    # 找 "## 📈 因子历史走势" section
    m = re.search(r"## 📈 因子历史走势.*?\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if not m:
        return None
    section = m.group(1)

    # 表行 "| 20260731 | ¥103.4 | A/M/A | ..."
    data_lines = [
        ln for ln in section.splitlines()
        if ln.startswith("| ") and not ln.startswith("|---") and "日期" not in ln
    ]
    if not data_lines:
        return None

    # 第一个 data 行 (逆序后最新日期在最上面)
    last = data_lines[0]
    cells = [c.strip() for c in last.strip("|").split("|")]
    if len(cells) < 9:
        return None
    day_score_raw = cells[9].strip() if len(cells) > 9 else "—"
    day_top, day_bot = _parse_day_score(day_score_raw)
    return {
        "date": cells[0],
        "close": cells[1],
        "wyckoff": cells[2],
        "sub_event": cells[3],
        "ma": cells[4],
        "hub_daily": cells[5],
        "hub_weekly": cells[6],
        "bsp": cells[7],
        "chg": cells[8],
        "accum_days": cells[10].strip() if len(cells) > 10 else "—",
        "day_top": day_top,
        "day_bot": day_bot,
        "day_score": max(day_top, day_bot),
    }


def _parse_day_score(raw: str) -> tuple[int, int]:
    """'4/6' → (4, 6), '5' → (5, 0), '—' → (0, 0)
    用于"日分(顶/底)"列的解析
    """
    if not raw or raw == "—":
        return 0, 0
    if "/" in raw:
        parts = raw.split("/")
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return 0, 0
    if raw.isdigit():
        return int(raw), 0  # 旧格式单数字算顶分
    return 0, 0


def _parse_md_factor_history(md_path: Path) -> list[dict]:
    """读整个因子历史表格 (60+ 行), 提取 date + day_top + day_bot

    Returns:
        [{'date': '20260511', 'day_top': 4, 'day_bot': 6, 'week': '2026-W20'}, ...]
    """
    if not md_path.exists():
        return []
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return []
    m = re.search(r"## 📈 因子历史走势.*?\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if not m:
        return []
    out = []
    for ln in m.group(1).splitlines():
        if not ln.startswith("| ") or ln.startswith("|---") or "日期" in ln:
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < 12:
            continue
        ds = cells[0]
        day_raw = cells[11].strip() if len(cells) > 11 else "—"
        top, bot = _parse_day_score(day_raw)
        if top == 0 and bot == 0:
            continue  # 没分
        # 算 ISO week
        from datetime import datetime
        d_clean = ds.replace("-", "")[:8]
        try:
            dt = datetime.strptime(d_clean, "%Y%m%d")
            wk = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        except ValueError:
            continue
        out.append({"date": ds, "day_top": top, "day_bot": bot, "week": wk})
    return out


def compute_market_state(
    weekly_scores: dict[str, list[tuple[int, int]]],
) -> dict[str, dict]:
    """按周聚合所有票的日分, 判定市场状态 (牛/熊/震荡)

    Args:
        weekly_scores: {week: [(top, bot), ...]} 每只票每日顶/底分元组

    Returns:
        {week: {'state': '牛/熊/震荡', 'score': 0-1, 'metrics': {...}}}
    """
    from collections import defaultdict
    result = {}
    # 4 周滚动窗口
    sorted_weeks = sorted(weekly_scores.keys())
    for i, wk in enumerate(sorted_weeks):
        # 取本周 + 前 3 周 (4 周窗口)
        window_weeks = sorted_weeks[max(0, i-3):i+1]
        # weekly_scores 现存的值是 (top, bot) 元组, 取顶分判定熊牛
        window_top = [t for w in window_weeks for (t, _b) in weekly_scores.get(w, [])]
        window_bot = [b for w in window_weeks for (_t, b) in weekly_scores.get(w, [])]
        if not window_top:
            continue
        n = len(window_top)
        avg = sum(window_top) / n
        n_ge10 = sum(1 for s in window_top if s >= 10)
        n_ge15 = sum(1 for s in window_top if s >= 15)
        avg_bot = sum(window_bot) / n if window_bot else 0
        # 加权打分
        if n >= 20:
            a_score = 1.0 if n_ge10 / n >= 0.15 else (0.5 if n_ge10 / n >= 0.05 else 0.0)
        else:
            a_score = 0.5 if n_ge10 >= 5 else 0.0
        b_score = 1.0 if n >= 20 and n_ge15 / n >= 0.08 else (0.5 if n_ge15 >= 2 else 0.0)
        c_score = 1.0 if avg >= 5 else (0.5 if avg >= 3 else 0.0)
        total = a_score * 0.4 + b_score * 0.3 + c_score * 0.3
        if total >= 0.6:
            state = "熊"
        elif total >= 0.3:
            state = "震荡"
        else:
            state = "牛"
        result[wk] = {
            "state": state,
            "score": round(total, 2),
            "metrics": {
                "avg_top": round(avg, 1),
                "avg_bot": round(avg_bot, 1),
                "n_top_ge10": n_ge10,
                "n_top_ge15": n_ge15,
                "n_total": n,
            },
        }
    return result


def _parse_md_last_signal(md_path: Path) -> list[str]:
    """从 md 的 "## 📈 因子历史走势" section 末行读今日新触发的信号

    注意: 不能扫全部行——历史行里的 🆕 是当天首次触发，扫全部会把 3 个月前的旧信号混入
    """
    if not md_path.exists():
        return []
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return []

    m_fh = re.search(r"## 📈 因子历史走势.*?\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if not m_fh:
        return []
    data_lines = [
        ln for ln in m_fh.group(1).splitlines()
        if ln.startswith("| ") and not ln.startswith("|---") and "日期" not in ln
    ]
    if not data_lines:
        return []
    last = data_lines[0]  # 末行是最新日期
    cells = [c.strip() for c in last.strip("|").split("|")]
    if len(cells) >= 12:
        chg = cells[10]
        if chg and chg != "—":
            return [chg[:120]]
    return []


def _sig_weight(detail: str, direction: str) -> int:
    """给信号字符串打权重，用于排序（越高越重要）

    2026-08-05 修: 入口先 strip 掉 ❌...消失(...) 段, 消失的信号不参与权重
    原 bug: ❌🟢1买⭐消失(60m) 含 "1买⭐" 字符串, 被当触发加权 30 分
    实际语义: 1买⭐ 今天消失了, 不是新触发, 不应加分
    """
    import re
    # 把 ❌...(period) 整段去掉, period 含 "60m"/"daily"/"weekly"
    clean = re.sub(r'❌[^(]*\([^)]*\)', '', detail)
    score = 0
    if direction == "sell":
        if "趋势1卖" in clean or "1卖⭐" in clean: score += 30
        elif "1卖" in clean:                          score += 20
        if "⭐趋势顶背" in clean:                     score += 25
        elif "🔵普通顶背" in clean:                   score += 15
        elif "🟡盘整顶背" in clean:                   score += 5
        if "UTAD" in clean or "DistributionStart" in clean: score += 20
        if "跌出中枢" in clean:                       score += 15
        if "跌进中枢" in clean:                       score += 8
        if "背驰🔴" in clean:                         score += 12
    else:
        if "趋势1买" in clean or "1买⭐" in clean:  score += 30
        elif "1买" in clean:                          score += 20
        if "⭐趋势底背" in clean:                     score += 25
        elif "🔵普通底背" in clean:                   score += 15
        elif "🟡盘整底背" in clean:                   score += 5
        if "SOS" in clean or "Spring" in clean:      score += 20
        if "LPS" in clean:                            score += 12
        if "止跌" in clean:                           score += 8
    return score


def _importance_label_bot(score: int) -> str:
    """按底分分档 (10 天合集)"""
    if score >= 20: return "🟢🟢 极强建仓"
    if score >= 15: return "🟢 强建仓"
    if score >= 10: return "🟡 偏强"
    return "⬜ 观察"


def _importance_label_top(score: int) -> str:
    """按顶分分档 (10 天合集)"""
    if score >= 20: return "🔴🔴 极强逃顶"
    if score >= 15: return "🔴 强逃顶"
    if score >= 10: return "🟠 偏强"
    return "🟡 观察"


def _importance_label(detail: str, direction: str) -> str:
    """根据信号内容返回重要性标签"""
    w = _sig_weight(detail, direction)
    if direction == "sell":
        if w >= 40: return "🔴🔴 极强逃顶"
        if w >= 25: return "🔴 强逃顶"
        if w >= 15: return "🟠 偏强"
        return "🟡 观察"
    else:
        if w >= 40: return "🟢🟢 极强建仓"
        if w >= 25: return "🟢 强建仓"
        if w >= 15: return "🟡 偏强"
        return "⬜ 观察"


def _build_rows(codes_names: list[tuple[str, str]], n_days: int = 10, threshold: int = 6) -> tuple[list, list, list, dict]:
    """最近 N 天合集 (user 拍板 1 天 → 10 天, 2026-08-21)

    信号列填**具体信号名** (不是数字): "🆕1买(60m) ✅LPS(daily)"
    排序按 day_top/day_bot 数字 (权重)

    buy_rows / sell_rows 每项: (date, code, name, score, signal_str)
    all_table_rows 每项: (code, name, last_row_dict, has_sig) —— 14 列原格式

    实现: RawContext.from_dump → analyze_history → compute_factor_history(history=...)
            → score_top/bot_signals 重算
    (md 文件没存 10 天每日信号字符串, 必须重算)
    """
    import sys
    sys.path.insert(0, ".")
    from tools.analysis.analysis_engine import RawContext
    from tools.analysis.analysis_result_signals import (
        compute_factor_history, diff_rows, score_top_signals, score_bottom_signals
    )
    from tools.analysis.analysis_engine import AnalysisEngine
    from collections import defaultdict
    buy_rows, sell_rows, all_table_rows = [], [], []
    weekly_scores: dict[str, list[tuple[int, int]]] = defaultdict(list)
    n_ok = 0
    n_skip = 0
    for code, name in codes_names:
        # 从 portfolio/ 或 watchlist/ 找 md 文件
        md_path = (DOCS_DIR / "portfolio" / f"analyze-{code}-{name}.md")
        if not md_path.exists():
            md_path = (DOCS_DIR / "watchlist" / f"analyze-{code}-{name}.md")
        if not md_path.exists():
            n_skip += 1
            continue
        last = _parse_md_last_row(md_path)
        if not last:
            n_skip += 1
            continue
        n_ok += 1

        # 完整状态表 (14 列原格式, 用最新一行 last_row)
        has_sig = "⭐" if any(t in last["bsp"] for t in ("0买", "1买", "2买", "3买")) else ""
        all_table_rows.append((code, name, last, has_sig))

        # 收集全 60 天因子历史的日分 (用于市场状态判定, 仍从 md 读 0 重算)
        for row in _parse_md_factor_history(md_path):
            weekly_scores[row["week"]].append((row["day_top"], row["day_bot"]))

        # 10 天合集: 重算每日具体信号 (md 没存 10 天每日信号字符串)
        try:
            from tools.kline_store import DataStore
            from tools.analysis.analysis_engine import AnalysisEngine
            ctx = DataStore.get_ctx(code)
            if not ctx.kline:
                continue
            all_dates = [k['trade_date'].replace('-','')[:8] for k in ctx.kline]
            n_days_idx = min(n_days, len(all_dates))
            dates = all_dates[-n_days_idx:]
            history = AnalysisEngine().analyze_history(ctx, dates)
            rows = compute_factor_history(ctx, step=1, lookback=n_days, history=history)
            for i in range(1, len(rows)):
                prev, cur = rows[i - 1], rows[i]
                changes = diff_rows(prev, cur)
                date = cur.get("date", "")
                if not date:
                    continue
                top = score_top_signals(changes, cur, prev)
                bot = score_bottom_signals(changes, cur, prev)
                # 底信号 (按信号权重 ≥ threshold, 阈值=6 是 sum 形式但实际触发是 6+)
                if bot["score"] >= threshold:
                    sig_names = [s[2] for s in bot["signals"][:5]]  # 取前 5 个信号
                    sig_str = " ".join(sig_names) if sig_names else f"底{bot['score']}"
                    # 用 _sig_weight 算权重 (按 _sig_weight 加权, 信号越多权重越高)
                    weight = sum(_sig_weight(s, "buy") for s in sig_names) if sig_names else bot["score"]
                    if weight >= threshold:
                        buy_rows.append((date, code, name, weight, sig_str))
                # 顶信号
                if top["score"] >= threshold:
                    sig_names = [s[2] for s in top["signals"][:5]]
                    sig_str = " ".join(sig_names) if sig_names else f"顶{top['score']}"
                    weight = sum(_sig_weight(s, "sell") for s in sig_names) if sig_names else top["score"]
                    if weight >= threshold:
                        sell_rows.append((date, code, name, weight, sig_str))
        except Exception as e:
            print(f"  {code} {name} 重算失败: {e}")
    print(f"  读 md 成功: {n_ok}, 跳过: {n_skip} (10天窗口 + 阈值≥{threshold}, 重算 signal 名)")
    market_state = compute_market_state(dict(weekly_scores))
    return buy_rows, sell_rows, all_table_rows, market_state


def _render_md(buy_rows, sell_rows, all_table_rows, total_watchlist: int,
               market_state: dict) -> str:
    today = datetime.date.today().isoformat()
    # 当前周市场状态
    cur_wk = f"{datetime.datetime.now().isocalendar()[0]}-W{datetime.datetime.now().isocalendar()[1]:02d}"
    cur_state = market_state.get(cur_wk, {})

    state_emoji = {"牛": "🟢", "震荡": "🟡", "熊": "🔴"}
    state_line = ""
    if cur_state:
        s = cur_state["state"]
        m = cur_state["metrics"]
        state_line = (
            f"\n## 🌐 大盘市场状态: {state_emoji.get(s, '⚪')}{s} "
            f"(score={cur_state['score']}, 4周窗口: {m['n_total']}条记录, "
            f"顶均分={m['avg_top']}, 底均分={m['avg_bot']}, ≥10顶:{m['n_top_ge10']}, ≥15顶:{m['n_top_ge15']})\n"
        )
    else:
        state_line = "\n## 🌐 大盘市场状态: ⚪数据不足\n"

    lines = [
        f"# 全量扫描 {today}\n",
        f"> {total_watchlist} 只票 | 数据来自 docs/portfolio/*.md + docs/watchlist/*.md (t_analyze_all 阶段已生成) | 0 重算, 0 from_raw\n",
        state_line,
    ]

    # ---- 底部信号 (4 列: 重要性/代码/名称/信号, 信号列含日期) ----
    lines.append("---\n\n## 🟢 底部建仓信号 (最近 10 天所有 ≥ 6, 按权重降序)\n\n")
    if buy_rows:
        sorted_buy = sorted(buy_rows, key=lambda x: (-x[3], -int(x[0])))
        lines += [
            "| 重要性 | 代码 | 名称 | 信号 |\n",
            "|--------|------|------|------|\n",
        ]
        for date, code, name, score, detail in sorted_buy:
            label = _importance_label_bot(score)
            sig_with_date = f"{date} {detail}"
            lines.append(f"| {label} | {code} | {name} | {sig_with_date} |\n")
    else:
        lines.append("_最近 10 天无底部强信号_\n")

    # ---- 顶部信号 (4 列: 重要性/代码/名称/信号, 信号列含日期) ----
    lines.append("\n---\n\n## 🔴 顶部逃顶信号 (最近 10 天所有 ≥ 6, 按权重降序)\n\n")
    if sell_rows:
        sorted_sell = sorted(sell_rows, key=lambda x: (-x[3], -int(x[0])))
        lines += [
            "| 重要性 | 代码 | 名称 | 信号 |\n",
            "|--------|------|------|------|\n",
        ]
        for date, code, name, score, detail in sorted_sell:
            label = _importance_label_top(score)
            sig_with_date = f"{date} {detail}"
            lines.append(f"| {label} | {code} | {name} | {sig_with_date} |\n")
    else:
        lines.append("_最近 10 天无顶部强信号_\n")

    # 完整状态表 (含日分顶/底)
    lines += [
        "\n---\n\n## 完整状态表 (含今日信号分, 跟单只 md 因子历史走势最后一行对齐)\n\n",
        "| 代码 | 名称 | 收盘 | 威科夫(日/周) | 子事件(日/周) | MA(日/周) | 日中枢 | 周中枢 | 买卖点 | 信号 | 日分(顶/底) | A天(日/周) |\n",
        "|------|------|------|--------------|-------------|-----------|--------|--------|--------|------|-------------|------------|\n",
    ]
    all_table_rows.sort(key=lambda x: (0 if x[-1] == "⭐" else 1, x[0]))
    for code, name, last, has_sig in all_table_rows:
        top = last.get("day_top", 0)
        bot = last.get("day_bot", 0)
        if top or bot:
            ds_str = f"**{top}**/{bot}" if top >= 10 else f"{top}/{bot}"
        else:
            ds_str = "—"
        lines.append(
            f"| {code} | {name}{has_sig} | {last['close']} | "
            f"{last['wyckoff']} | {last['sub_event']} | {last['ma']} | "
            f"{last['hub_daily']} | {last['hub_weekly']} | "
            f"{last['bsp']} | {last['chg']} | {ds_str} | {last.get('accum_days','—')} |\n"
        )

    # 历史市场状态 (最近 12 周)
    if market_state:
        recent = sorted(market_state.keys())[-12:]
        lines += [
            "\n---\n\n## 📈 历史市场状态 (近 12 周)\n\n",
            "| 周 | 状态 | score | 顶均分 | 底均分 | ≥10顶 | ≥15顶 | 总数 |\n",
            "|---|---|---|---|---|---|---|---|\n",
        ]
        for wk in recent:
            s = market_state[wk]
            m = s["metrics"]
            lines.append(
                f"| {wk} | {state_emoji.get(s['state'], '⚪')}{s['state']} "
                f"| {s['score']} | {m['avg_top']} | {m['avg_bot']} | {m['n_top_ge10']} | {m['n_top_ge15']} | {m['n_total']} |\n"
            )

    lines.append(f"\n---\n> 生成时间: {datetime.datetime.now().strftime('%H:%M:%S')}\n")
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description="批量扫描 watchlist, 输出有 代码+名称+场景+信号 的 batch md (直接读 md 文件, 0 重算)")
    parser.add_argument("codes", nargs="*", help="指定 codes (e.g. 300274 600089)")
    parser.add_argument("--all", action="store_true", help="跑 watchlist 全部 (默认)")
    parser.add_argument("--sector", help="板块筛选 (e.g. CPO, 光模块, 半导体)")
    parser.add_argument("--out", help="输出 markdown 路径, 默认 docs/signal-watchlist.md (方案 A, 2026-08-20)")
    args = parser.parse_args()

    watchlist = _load_watchlist()
    if args.codes:
        name_map = {s["code"]: s["name"] for s in watchlist}
        codes_names = [(c, name_map.get(c, c)) for c in args.codes]
        total = len(codes_names)
    elif args.sector:
        sub = _filter_by_sector(watchlist, args.sector)
        codes_names = [(s["code"], s["name"]) for s in sub]
        total = len(codes_names)
        print(f"📊 板块 {args.sector}: {total} 只")
    else:
        codes_names = [(s["code"], s["name"]) for s in watchlist]
        total = len(codes_names)

    if not codes_names:
        print("❌ 没找到 codes (--sector 没匹配?)")
        sys.exit(1)

    print(f"📊 跑 {total} 只 batch summary (直接读 md 文件, 0 重算)...")
    buy_rows, sell_rows, all_table_rows, market_state = _build_rows(codes_names)

    md = _render_md(buy_rows, sell_rows, all_table_rows, total, market_state)
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = DOCS_DIR / "signal-watchlist.md"  # 方案 A (2026-08-20): 单文件, 每天覆盖
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")

    n_with_sig = sum(1 for r in all_table_rows if r[-1] == "⭐")
    sig_codes = [r[0] for r in all_table_rows if r[-1] == "⭐"]
    print(f"FILE: {out_path}")
    print(f"SUMMARY: 共 {len(all_table_rows)} 只, {n_with_sig} 只有今日信号")
    if sig_codes:
        print(f"SIG_CODES: {', '.join(sig_codes)}")


if __name__ == "__main__":
    main()
