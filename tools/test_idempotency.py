"""
test_idempotency.py — render_report 幂等性测试
用法: PYTHONPATH=. python3 tools/test_idempotency.py [code]
"""
import hashlib, sys
from tools.data_store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.analysis_data import AnalysisData
from tools.render.report_renderer import render_report

def test(code: str = "300274", runs: int = 3) -> bool:
    ctx = DataStore.get_ctx(code)
    if not ctx.kline:
        print(f"❌ {code} 本地无K线")
        return False
    hashes = set()
    for i in range(runs):
        _last = ctx.kline[-1]["trade_date"].replace("-", "")[:8] if ctx.kline else ""
        result = AnalysisEngine().analyze_history(ctx, [_last]).get(_last)
        data = AnalysisData.from_result(ctx, result)
        md = render_report(data)
        hashes.add(hashlib.md5(md.encode()).hexdigest())
    ok = len(hashes) == 1
    print(f"{'✅' if ok else '❌'} {code}: {runs}次渲染 {'完全一致' if ok else '不一致 ' + str(hashes)}")
    return ok

if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "300274"
    sys.exit(0 if test(code) else 1)
