"""tools/sync_stock.py — 同步单只股票数据

内部走 DataStore + parquet (data/history/daily/*.parquet)
职责: 仅拉数据写 parquet，不负责分析/渲染。

用法:
  python -m tools.sync_stock 300308           # 同步增量
"""
import argparse
from pathlib import Path


def main():
    from tools.history_sync import sync_incremental
    from tools.data_store import DataStore

    parser = argparse.ArgumentParser()
    parser.add_argument("code", help="股票代码 (如 300274)")
    args = parser.parse_args()

    # L0: 增量同步 (幂等, 无缺口秒返回)
    print("🔄 同步K线历史...")
    sync_incremental()

    # L1: 读数据（验证）
    print(f"📊 验证: {args.code}")
    ctx = DataStore.get_ctx(args.code)
    if not ctx.kline:
        print(f"⚠️ {args.code} 本地无K线, 请先跑: python -m tools.history_sync --init")
        return

    print(f"  - 价: ¥{ctx.current_price}")
    print(f"  - K线: {len(ctx.kline)} 根")
    print(f"✅ 数据就绪 ({args.code})")


if __name__ == "__main__":
    main()
