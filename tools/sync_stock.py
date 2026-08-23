"""tools/sync_stock.py — 同步单只股票数据并分析

内部走 DataStore + parquet (data/history/daily/*.parquet)

用法:
  python -m tools.sync_stock 300308           # 同步增量 + 打印分析
  python -m tools.sync_stock 300308 --render  # 同步 + 渲染报告
"""
import argparse
from pathlib import Path


def main():
    from tools.history_sync import sync_incremental
    from tools.data_store import DataStore
    from tools.analysis.analysis_engine import AnalysisEngine
    from tools.analysis.analysis_data import AnalysisData

    parser = argparse.ArgumentParser()
    parser.add_argument("code", help="股票代码 (如 300274)")
    parser.add_argument("--render", action="store_true", help="渲染报告")
    args = parser.parse_args()

    # L0: 增量同步 (幂等, 无缺口秒返回)
    print("🔄 同步K线历史...")
    sync_incremental()

    # L1: 读数据
    print(f"📊 分析: {args.code}")
    ctx = DataStore.get_ctx(args.code)
    if not ctx.kline:
        print(f"⚠️ {args.code} 本地无K线, 请先跑: python -m tools.history_sync --init")
        return

    print(f"  - 价: ¥{ctx.current_price}")
    print(f"  - K线: {len(ctx.kline)} 根")

    # L2: 跑分析
    engine = AnalysisEngine()
    result = engine.analyze(ctx)
    print(f"  - 场景: {result.scene} | 总分: {result.total_score:+.2f}")

    # L3: 渲染报告（可选）
    if args.render:
        from tools.render.report_renderer import render_report
        print(f"\n🎨 渲染报告...")
        data = AnalysisData.from_result(ctx, result)
        md = render_report(data)
        name = ctx.name or args.code
        report_path = Path(__file__).parent.parent / "docs" / f"analyze-{args.code}-{name}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(md, encoding="utf-8")
        print(f"✅ 报告已存: {report_path} ({len(md)} chars)")


if __name__ == "__main__":
    main()
