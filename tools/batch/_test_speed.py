"""开发测速脚本: 验证 DataStore.load_all_kline 性能 (替代原 duckdb 直读)"""
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

t0 = time.time()
from tools.storage.store import DataStore
kline_dict = DataStore.load_all_kline(years=5.5)
print(f"codes: {len(kline_dict):,}, time: {time.time()-t0:.1f}s")
