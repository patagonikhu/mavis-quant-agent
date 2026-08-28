"""
batch_matrix.py — 批量跑 N 只股票 因子 × 3 周期 矩阵 (2026-07-30 v1.0, 2026-08-17 改名)

**用户原话**:
- '你就的每个section 需要单独的方法, 这样可以复用' → 不调 render_report 22 section
- '只读现成 markdown 的 因子矩阵 section' → 但不走 awk 提 md (hack)
- 应该有 render 方法直接输出 因子矩阵 section 字符串

**正确做法**:
- _section_factor_matrix 已经在 report_renderer 存在 (2026-08-17 改名前 _section_5method_matrix)
- build_factor_matrix + render_factor_matrix_md 已经在 tools/analysis/factor_matrix 公开
- 本文件: 批量调 build_factor_matrix, 各自调 render_factor_matrix_md, 拼成 1 份 batch report

**用法**:
  python -m tools.batch_matrix 300274 600089 002475 002028 601138 600362
  python -m tools.batch_matrix --holdings
  python -m tools.batch_matrix --sector CPO
  python -m tools.batch_matrix --all
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
WATCHLIST_JSON = PROJECT_ROOT / "data" / "watchlist.json"
DOCS_DIR = PROJECT_ROOT / "docs"

# 默认 6 只持仓 (跟 watchlist 持仓顺序一致)
DEFAULT_HOLDINGS = ['300274', '600089', '002475', '002028', '601138', '600362']


def _resolve_codes(args) -> list[str]:
    """根据 CLI 参数拿 codes 列表"""
    if args.codes:
        return args.codes
    if args.holdings:
        return DEFAULT_HOLDINGS
    if args.all:
        wl = json.load(open(WATCHLIST_JSON))
        return [s['code'] for s in wl.get('stocks', [])]
    if args.sector:
        # 板块筛选: 从 watchlist 找 industry / note 匹配的
        wl = json.load(open(WATCHLIST_JSON))
        codes = []
        sector_kw = args.sector  # e.g. "CPO", "光模块"
        for s in wl.get('stocks', []):
            industry = s.get('industry', '')
            note = s.get('note', '')
            tags = s.get('tags', [])
            if sector_kw in industry or sector_kw in note or sector_kw in tags:
                codes.append(s['code'])
        return codes
    # 默认持仓
    return DEFAULT_HOLDINGS


def render_one_matrix(code: str) -> str:
    """单只 因子 × 3 周期 矩阵 section 字符串 (走现成 render, 2026-08-17 改名前 5 方法)

    不调 render_report (22 section), 调 build_factor_matrix + render_factor_matrix_md
    1 个 section, 0 重复计算

    v5.10.30 改: 走 AnalysisEngine.analyze_dump_to_dict() (内含 factor.* 3 周期桥接)
    v5.10.26 改: dump.get("signals_5method") 字段已删, 改用 AnalysisEngine
    v5.10.42 改: 删 RenderData.signals_5method property 兼容层 (5 处全部改读 data.analysis)
    """
    from tools.kline_store import DataStore
    from tools.analysis.analysis_engine import AnalysisEngine

    ctx = DataStore.get_ctx(code)
    if not ctx.kline:
        return f"## {code} 跳过\n\n> ❌ 本地无K线, 跑 `history_sync --init`\n"

    name = ctx.name or code
    current_price = ctx.current_price or 0
    _last = ctx.kline[-1]["trade_date"].replace("-", "")[:8] if ctx.kline else ""
    result = AnalysisEngine().analyze_history(ctx, [_last]).get(_last)
    s5 = result.to_dict(ctx)
    chan_data = s5.get("chan") or {}
    buy_sell_points = s5.get("buy_sell_points") or {}

    if s5.get("error"):
        return f"## {code} 跳过 (AnalysisEngine 失败)\n\n> ❌ {s5['error']}\n\n---\n\n"

    # 走 build_factor_matrix + render_factor_matrix_md
    try:
        from tools.analysis.factor_matrix import (
            build_factor_matrix,
            render_factor_matrix_md,
        )
        matrix = build_factor_matrix(
            code=code,
            name=name,
            current_price=current_price,
            signals_5method=s5,  # build_factor_matrix 内部读 scene/signals_active/score 等
            chan_data=chan_data,
            buy_sell_points=buy_sell_points,
        )
        md = render_factor_matrix_md(matrix)
        return f"## 🎯 {code} {name} (¥{current_price})\n\n{md}\n\n---\n\n"
    except Exception as e:
        return f"## {code} 渲染失败 (build_factor_matrix)\n\n> ❌ {type(e).__name__}: {str(e)[:200]}\n\n---\n\n"


def render_batch(codes: list[str], title: str = "批量 因子 × 3 周期 矩阵") -> str:
    """批量渲染: 1 份 markdown, N 段 因子矩阵 section"""
    parts = [
        f"# {title}\n",
        f"\n> 自动生成: {datetime.now().strftime('%Y-%m-%d %H:%M')} | {len(codes)} 只票\n",
        f"\n> 走 t-batch 流程: build_factor_matrix + render_factor_matrix_md, 0 重复计算\n",
        "\n---\n\n",
    ]

    n_ok = 0
    n_skip = 0
    for code in codes:
        md = render_one_matrix(code)
        if "渲染失败" in md or "跳过" in md:
            n_skip += 1
        else:
            n_ok += 1
        parts.append(md)

    parts.append(f"\n---\n\n## 汇总\n\n")
    parts.append(f"- 成功: **{n_ok}** ✅\n")
    parts.append(f"- 跳过: **{n_skip}** ⏭️\n")
    parts.append(f"\n每段 因子 × 3 周期 矩阵都走 `tools/analysis/factor_matrix` 公开接口, 0 重新拼\n")

    return "".join(parts)


def save_batch(codes: list[str], out_path: Path, title: str = None) -> Path:
    """渲染 + 写盘, 返 out_path"""
    if title is None:
        title = f"批量 因子 × 3 周期 ({len(codes)} 只) - {datetime.now().strftime('%Y-%m-%d')}"
    md = render_batch(codes, title=title)
    out_path.write_text(md, encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="批量跑 N 只股票 因子 × 3 周期 矩阵 (走现成 render 方法, 0 重新拼)"
    )
    parser.add_argument("codes", nargs="*", help="指定 codes (e.g. 300274 002475)")
    parser.add_argument("--holdings", action="store_true", help="跑 6 只默认持仓")
    parser.add_argument("--all", action="store_true", help="跑 watchlist 全部")
    parser.add_argument("--sector", help="板块筛选 (e.g. CPO, 光模块, 半导体)")
    parser.add_argument("--out", help="输出 markdown 路径, 默认 docs/batch-{date}.md")
    args = parser.parse_args()

    codes = _resolve_codes(args)
    if not codes:
        print("❌ 没找到 codes (--sector 没匹配?)")
        sys.exit(1)

    if args.out:
        out_path = Path(args.out)
    else:
        date = datetime.now().strftime("%Y-%m-%d")
        suffix = "-".join(codes[:3]) + ("..." if len(codes) > 3 else "")
        out_path = DOCS_DIR / f"batch-{suffix}-{date}.md"

    print(f"📊 跑 {len(codes)} 只: {codes[:3]}{'...' if len(codes) > 3 else ''}")
    save_batch(codes, out_path)
    print(f"✅ 写盘: {out_path}")


if __name__ == "__main__":
    main()
