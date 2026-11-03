"""从已生成 md 的因子矩阵表反推每一票 (chan / wyckoff / smc / res)

用途: 量化「SMC 这票对综合判定的影响」, 决定 SMC 从 md 删除时投票怎么改。
跑法: bash tools/with_venv.sh python -m tools.batch._eval_smc_vote
"""
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"

ROW_CHAN = re.compile(r"^\| \*\*缠论[^|]*\| (.*?) \| (.*?) \|$")
ROW_WY = re.compile(r"^\| \*\*威科夫[^|]*\| (.*?) \| (.*?) \|$")
ROW_SMC = re.compile(r"^\| \*\*SMC[^|]*\| (.*?) \| (.*?) \|$")
ROW_COMP = re.compile(r"^\| \*\*🎯 综合判定\*\* \| (.*?) \| (.*?) \|$")


def chan_vote(cell: str):
    """chan 底/顶票: 只看买卖点关键词"""
    s = cell
    buy = any(k in s for k in ('1买', '2买', '0买'))
    sell = any(k in s for k in ('1卖', '2卖', '3卖'))
    return buy, sell


def wy_vote(cell: str):
    s = cell
    return ('Accumulation' in s, 'Distribution' in s)


def smc_vote_md(cell: str):
    """md 只暴露 多OB/空OB/扫流; FVG 不可见 → 返回 (可见底票, 可见顶票, 有FVG未知)"""
    s = cell
    bull = '多OB' in s
    bear = '空OB' in s
    return bull, bear


def parse_bot(comp: str):
    m = re.search(r"底(\d)/顶(\d)", comp)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def main():
    files = sorted(DOCS.rglob("*.md"))
    stats = Counter()
    detail = []
    for f in files:
        try:
            lines = f.read_text(encoding='utf-8').splitlines()
        except Exception:
            continue
        rows = {}
        for ln in lines:
            for key, pat in (('chan', ROW_CHAN), ('wy', ROW_WY), ('smc', ROW_SMC), ('comp', ROW_COMP)):
                if key in rows:
                    continue
                m = pat.match(ln)
                if m:
                    rows[key] = (m.group(1), m.group(2))
        if len(rows) < 4:
            continue
        stats['files'] += 1
        for idx, period in ((0, 'weekly'), (1, 'daily')):
            chan_c, wy_c, smc_c, comp_c = (rows[k][idx] for k in ('chan', 'wy', 'smc', 'comp'))
            bot, top = parse_bot(comp_c)
            if bot is None:
                continue
            cbuy, csell = chan_vote(chan_c)
            wbuy, wtop = wy_vote(wy_c)
            sbuy, stop = smc_vote_md(smc_c)
            visible_bottom = int(cbuy) + int(wbuy) + int(sbuy)
            visible_top = int(csell) + int(wtop) + int(stop)
            stats['periods'] += 1
            stats[f'reported_bot_{bot}'] += 1
            stats[f'reported_top_{top}'] += 1
            if visible_bottom == bot and visible_top == top:
                stats['match'] += 1
                stats[f'match_bot_{bot}'] += 1
            else:
                # 差额只能用 FVG (md 不可见) 解释
                stats['mismatch'] += 1
                stats[f'mismatch_bot_{bot}_visible_{visible_bottom}'] += 1
            if bot >= 2:
                stats['bot_ge2'] += 1
                stats[f'bot_ge2_combo_c{cbuy}w{wbuy}s{sbuy}'] += 1
            detail.append((f.name, period, bot, top, cbuy, wbuy, sbuy, visible_bottom))

    print("=== 汇总 ===")
    for k in sorted(stats):
        print(f"{k}: {stats[k]}")
    print("\n=== 底>=2 的组合明细 (c=缠论 w=威科夫 s=SMC可见OB) ===")
    for k in sorted(stats):
        if k.startswith('bot_ge2_combo'):
            print(f"{k}: {stats[k]}")
    print("\n=== 底>=2 明细行 ===")
    for d in detail:
        if d[2] >= 2:
            print(d)


if __name__ == '__main__':
    main()
