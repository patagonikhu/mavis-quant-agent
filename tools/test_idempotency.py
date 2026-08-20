"""
test_idempotency.py — render_report 幂等性测试
用法: PYTHONPATH=. python3 tools/test_idempotency.py [code]
"""
import json, hashlib, sys
from pathlib import Path
from tools.analysis.analysis_data import AnalysisData
from tools.render.report_renderer import render_report

def test(code: str = "300274", runs: int = 3) -> bool:
    dump = Path(f"data/dump/{code}.json")
    if not dump.exists():
        print(f"❌ dump 不存在: {dump}")
        return False
    raw = json.loads(dump.read_text(encoding="utf-8"))
    hashes = set()
    for i in range(runs):
        data = AnalysisData.from_raw(raw)
        md = render_report(data)
        hashes.add(hashlib.md5(md.encode()).hexdigest())
    ok = len(hashes) == 1
    print(f"{'✅' if ok else '❌'} {code}: {runs}次渲染 {'完全一致' if ok else '不一致 ' + str(hashes)}")
    return ok

if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "300274"
    sys.exit(0 if test(code) else 1)
