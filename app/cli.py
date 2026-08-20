"""CLI 入口

用法：
    python -m app.cli scan                              # 全市场板块扫描
    python -m app.cli signal 半导体                     # 单板块信号分析
    python -m app.cli backtest                          # 个股信号回测（watchlist，90天）
    python -m app.cli backtest --codes 002371 688012    # 指定股票
    python -m app.cli backtest --days 180 --hold 5      # 自定义天数
    python -m app.cli backtest --force                  # 强制重拉 dump 数据
    python -m app.cli backtest-sector --days 30         # 板块信号回测（旧）
    python -m app.cli optimize --sector 半导体          # 参数优化
    python -m app.cli monitor                           # 启动实时监控调度器
    python -m app.cli feedback list                     # 查看用户反馈
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def _print_json(obj: dict | list) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---- 子命令实现 ----

async def cmd_scan(args) -> None:
    """全市场板块扫描，输出 Top10"""
    from app.data import get_data_provider
    from app.data.models import SECTOR_LIST, Period
    from app.signals import evaluate_sector
    from app.signals.leader import identify_leaders

    provider = get_data_provider()
    print(f"扫描 {len(SECTOR_LIST)} 个板块，请稍候...\n")

    results = []
    for sector_name in SECTOR_LIST:
        try:
            bars = await provider.get_sector_kline(sector_name, count=60)
            if len(bars) < 20:
                continue
            constituents = await provider.get_sector_constituents(sector_name)
            leaders = identify_leaders(constituents, top_n=3)
            leader_klines: dict = {}
            for ldr in leaders:
                lb = await provider.get_kline(ldr.symbol, Period.DAILY, 60)
                if lb:
                    leader_klines[ldr.symbol] = lb
            report = await evaluate_sector(
                sector_name=sector_name,
                sector_bars=bars,
                constituents=constituents,
                leader_klines=leader_klines,
            )
            results.append(report)
        except Exception as e:
            print(f"  ⚠ {sector_name} 失败: {e}", file=sys.stderr)

    results.sort(key=lambda r: r.total_score, reverse=True)
    top = results[:10]

    print("=" * 48)
    print("  A股板块启动信号扫描  Top 10")
    print("=" * 48)
    for i, r in enumerate(top, 1):
        icon = "🚨" if r.total_score >= 70 else "⚡" if r.total_score >= 50 else "📊"
        print(f"{i:2}. {icon} {r.sector_name:<10} {r.total_score:5.1f}/100  {r.rating}")
        if r.triggered_signals:
            sigs = " | ".join(ts["name"] for ts in r.triggered_signals)
            print(f"       触发: {sigs}")
    print()
    print("⚠  以上信号仅供研究参考，不构成投资建议。")


async def cmd_signal(args) -> None:
    """单板块信号详情"""
    sector_name: str = args.sector
    from app.data import get_data_provider
    from app.data.models import Period
    from app.signals import evaluate_sector
    from app.signals.leader import identify_leaders

    provider = get_data_provider()
    print(f"分析板块：{sector_name} ...\n")

    bars = await provider.get_sector_kline(sector_name, count=60)
    if len(bars) < 20:
        print(f"错误：{sector_name} K线数据不足（{len(bars)} 条），无法分析。")
        return

    constituents = await provider.get_sector_constituents(sector_name)
    leaders = identify_leaders(constituents, top_n=3)
    leader_klines: dict = {}
    for ldr in leaders:
        lb = await provider.get_kline(ldr.symbol, Period.DAILY, 60)
        if lb:
            leader_klines[ldr.symbol] = lb

    news_list = None
    try:
        news_list = await provider.get_news_realtime()
    except Exception:
        pass

    report = await evaluate_sector(
        sector_name=sector_name,
        sector_bars=bars,
        constituents=constituents,
        leader_klines=leader_klines,
        news_list=news_list,
    )

    print(report.summary_text())
    print()

    if leaders:
        print("【龙头股】")
        for c in leaders:
            print(f"  {c.name}（{c.symbol}）  流通市值 {c.circ_mv:.0f} 亿")
    print()

    if report.signal_details:
        print("【信号明细】")
        for name, sig in report.signal_details.items():
            status = "✓" if sig.triggered else "✗"
            print(f"  {status} {name:<28} {sig.reason}")

    print()
    print("⚠  以上信号仅供研究参考，不构成投资建议。")


async def cmd_backtest(args) -> None:
    """个股信号回测（watchlist，基于 dump 数据）"""
    from app.backtest.stock_engine import StockBacktestEngine

    codes = args.codes or None
    engine = StockBacktestEngine(
        lookback_days=args.days,
        hold_days=args.hold,
        force_dump=args.force,
    )

    print(f"个股信号回测：回看 {args.days} 天，持有 {args.hold} 天")
    if codes:
        print(f"股票：{', '.join(codes)}")
    else:
        print("股票：watchlist 全量")
    if args.force:
        print("⚠  --force 模式：重新拉取 dump 数据")
    print("运行中，请稍候...\n")

    records = engine.run(codes=codes)
    report  = engine.report(records)
    print(report.report_text())


async def cmd_backtest_sector(args) -> None:
    """板块信号回测（旧版）"""
    import datetime as dt
    from app.backtest.engine import BacktestEngine
    from app.data.models import SECTOR_LIST

    days: int = args.days
    sector: str = args.sector or ""

    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    sectors = [sector] if sector else SECTOR_LIST[:5]

    print(f"回测区间：{start} ~ {end}")
    print(f"板块：{', '.join(sectors)}")
    print("运行中，请稍候...\n")

    engine = BacktestEngine(start, end)
    metrics = await engine.run_and_report(sectors)
    print(metrics.report_text())
    print()
    print("⚠  回测结果不代表未来收益，仅供策略研究参考。")


async def cmd_optimize(args) -> None:
    """参数优化"""
    import datetime as dt
    from app.backtest.optimizer import optimize_weights
    from app.data.models import SECTOR_LIST

    sector: str = args.sector or ""
    days: int = getattr(args, "days", 60)

    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    sectors = [sector] if sector else SECTOR_LIST[:3]

    print(f"参数优化，区间：{start} ~ {end}，板块：{sectors}")
    print("运行中，请稍候...\n")

    result = await optimize_weights(start, end, sectors)
    print("最优权重配置：")
    _print_json(result["best_weights"])
    print(f"\n最优胜率：{result['best_win_rate']*100:.1f}%")
    print(f"最优平均涨幅：{result['best_avg_return']*100:.1f}%")

    if result.get("saved"):
        print("\n✓ 已保存至 data/best_params.json，下次运行自动加载。")


def cmd_monitor(args) -> None:
    """启动实时监控调度器（前台运行，Ctrl+C 退出）"""
    import signal as signal_mod

    from app.scheduler import start_scheduler, stop_scheduler

    start_scheduler()
    print("✓ 调度器已启动（盘中每5分钟扫描 | 15:30收盘分析 | 周日20:00回测）")
    print("  按 Ctrl+C 停止\n")

    loop = asyncio.get_event_loop()

    def _shutdown(sig, frame):
        print("\n正在停止调度器...")
        stop_scheduler()
        loop.stop()

    signal_mod.signal(signal_mod.SIGINT, _shutdown)
    signal_mod.signal(signal_mod.SIGTERM, _shutdown)

    try:
        loop.run_forever()
    finally:
        print("调度器已停止。")


async def cmd_feedback(args) -> None:
    """用户反馈管理"""
    from app.feedback import FeedbackStore

    store = FeedbackStore()
    sub = args.feedback_cmd

    if sub == "list":
        records = await store.list_recent(limit=20)
        if not records:
            print("暂无反馈记录。")
            return
        print(f"最近 {len(records)} 条反馈：")
        for r in records:
            icon = {"good": "👍", "bad": "👎", "neutral": "😐"}.get(r["rating"], "?")
            print(f"  {icon} [{r['date']}] {r['sector_name']} score={r['score']:.0f}  {r.get('comment', '')}")

    elif sub == "stats":
        stats = await store.get_stats()
        print("反馈统计：")
        _print_json(stats)

    else:
        print(f"未知子命令：{sub}，支持 list | stats")


# ---- 主入口 ----

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="A股板块启动信号 Agent CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    sub.add_parser("scan", help="全市场板块扫描，输出 Top 10 信号")

    # signal
    p_signal = sub.add_parser("signal", help="分析单个板块信号")
    p_signal.add_argument("sector", help="板块名称，如 半导体、AI、新能源车")

    # backtest（个股信号回测，主入口）
    p_bt = sub.add_parser("backtest", help="个股信号回测（watchlist，基于 dump）")
    p_bt.add_argument("--codes", nargs="+", default=[], help="指定股票代码，空则 watchlist 全量")
    p_bt.add_argument("--days",  type=int, default=90,  help="回看天数（默认90）")
    p_bt.add_argument("--hold",  type=int, default=10,  help="持有天数，用于胜率计算（默认10）")
    p_bt.add_argument("--force", action="store_true",   help="强制重新拉取 dump 数据")

    # backtest-sector（板块回测，旧版保留）
    p_bts = sub.add_parser("backtest-sector", help="板块信号回测（旧版）")
    p_bts.add_argument("--sector", default="", help="指定板块，空则取前5个")
    p_bts.add_argument("--days",   type=int, default=30, help="回测天数（默认30）")

    # optimize
    p_opt = sub.add_parser("optimize", help="信号权重参数优化")
    p_opt.add_argument("--sector", default="", help="指定板块，空则取前3个")
    p_opt.add_argument("--days", type=int, default=60, help="优化用历史天数（默认60）")

    # monitor
    sub.add_parser("monitor", help="启动实时监控调度器（前台运行）")

    # feedback
    p_fb = sub.add_parser("feedback", help="用户反馈管理")
    p_fb.add_argument("feedback_cmd", choices=["list", "stats"], help="list=列表, stats=统计")

    args = parser.parse_args()

    if args.command == "monitor":
        cmd_monitor(args)
    elif args.command == "feedback":
        asyncio.run(cmd_feedback(args))
    elif args.command == "scan":
        asyncio.run(cmd_scan(args))
    elif args.command == "signal":
        asyncio.run(cmd_signal(args))
    elif args.command == "backtest":
        asyncio.run(cmd_backtest(args))
    elif args.command == "backtest-sector":
        asyncio.run(cmd_backtest_sector(args))
    elif args.command == "optimize":
        asyncio.run(cmd_optimize(args))


if __name__ == "__main__":
    main()
