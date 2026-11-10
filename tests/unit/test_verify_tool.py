"""tools/verify.py —— 分层验证入口的纯逻辑单测

2026-11-10 加: L3 全量扫描要 112s, 不能靠跑全量来验证解析逻辑对不对。
把 md 解析抽成纯函数后, 这里用真实 md 样本覆盖, 成本 < 1s。
"""
from pathlib import Path

from tools.verify import parse_scan_md

ROOT = Path(__file__).resolve().parent.parent.parent

REAL_MD = ROOT / "docs" / "wyckoff-a2m.md"


def test_parse_real_scan_md():
    """真实扫描汇总 md 能抠出关键数字"""
    if not REAL_MD.exists():
        # 还没跑过扫描 → 用内联样本兜底
        md = (
            "| 原始候选 (scope=全市场) | — | — | 5459 → 5459 | 5459 |\n"
            "| **A→M 命中** | V13 严判 | lookback=5 | 3610 → 31 | **31** |\n"
            "| ├ standard 档 | 全过 | — | 31 → 4 | **4** |\n"
            "| └ loose 档 | 不达标 | — | 31 → 27 | **27** |\n"
            "| **T-38** | `002946` | 新乳业 | ... |\n"
        )
    else:
        md = REAL_MD.read_text(encoding="utf-8")

    d = parse_scan_md(md)
    assert d["hits"] == 31, f"命中数解析错: {d}"
    assert d["standard"] == 4, f"standard 解析错: {d}"
    assert d["loose"] == 27, f"loose 解析错: {d}"
    assert d["standard"] + d["loose"] == d["hits"], f"两档之和 ≠ 命中数: {d}"
    assert "002946" in d["codes"], f"代码没抠出来: {d['codes'][:5]}"


def test_parse_missing_fields_returns_default():
    """字段缺失不该抛异常 (返回 ? / 0), 免得 verify 在脏 md 上崩"""
    d = parse_scan_md("空文件")
    assert d["hits"] == "?"
    assert d["codes"] == []
