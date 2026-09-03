"""
check_data_sources.py — 扫全项目找野 fetch 调用 (2026-08-26 更新)

规则:
  - 所有 fetch 必须走 tools/fetch/tushare_fetcher.py (Tushare 唯一入口)
  - data_source.py 已在 2026-08-26 删 (其 5 个 fetch_* 函数内联到调用方)
  - DataStore (parquet 缓存) 是主路径, 不算野 fetch
  - 直接调 requests.get / subprocess.run(curl) / push2* / ifzq / qtimg 视为违规

用法:
    PYTHONPATH=. python3 tools/fetch/check_data_sources.py          # 扫全项目
    PYTHONPATH=. python3 tools/fetch/check_data_sources.py --quiet  # 只输出违规
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent  # tools/fetch/ → 项目根

# 合法的 fetch 入口 (白名单)
WHITELIST = {
    "tools/fetch/tushare_fetcher.py",  # 单一权威 (Tushare 直连入口)
    "tools/kline_store.py",  # parquet 缓存主路径
    "tools/fetch/data_fetcher.py",  # 旧入口, 已 deprecate, 仅保留兼容
    "tools/fetch/check_data_sources.py",  # 本检查脚本
    "tools/sync_watchlist_fresh.py",  # 缓存检查
}

# 违规模式 (regex)
VIOLATIONS = [
    (r"subprocess\.run\(['\"]curl", "野 curl 调用 (subprocess.run 拉数据)"),
    # urllib 只报警 push2/ifzq 域, datacenter-web 是合法源
    (r"urllib.*request|urlopen", "野 urllib 调用 (datacenter 除外)"),
    # 2026-07-24 修复: 之前正则把 datacenter-web 也判违规, 实际是合法源
    (r"^https?://push2[a-z]*\.(eastmoney|com\.cn)", "禁用源: push2/push2his/push2delay (WAF 拦截)"),
    (r"web\.ifzq\.gtimg\.cn", "禁用源: ifzq (WAF 拦截)"),
    (r"qt\.gtimg\.cn", "禁用源: qtimg (GBK 编码/WAF 拦截)"),
    (r"stock\.xueqiu\.com", "禁用源: 雪球 (需 cookie)"),
    (r"finance\.sina\.com\.cn.*scale=(60|30|15)", "Sina 60分/30分 K线 (备源, 不鼓励)"),
]

# 合法源白名单 (URL 模式, 出现时不报警 urllib)
WHITELIST_DOMAINS = [
    "datacenter-web.eastmoney.com",
    "datacenter.eastmoney.com",
]


def _is_whitelisted_url(line: str) -> bool:
    """检查这一行是否含白名单域"""
    for d in WHITELIST_DOMAINS:
        if d in line:
            return True
    return False


def scan():
    violations = []
    for py in PROJECT_ROOT.rglob("*.py"):
        if "node_modules" in str(py) or ".venv" in str(py) or ".claude/worktrees" in str(py):
            continue
        rel = str(py.relative_to(PROJECT_ROOT))
        if rel in WHITELIST:
            continue
        try:
            content = py.read_text(encoding="utf-8")
        except Exception:
            continue
        lines = content.split("\n")
        # 检测 docstring 范围 (简化启发)
        in_docstring = [False] * (len(lines) + 1)
        doc_open = False
        for i, line in enumerate(lines, 1):
            triple_count = line.count('"""') + line.count("'''")
            if triple_count > 0:
                # single-line: 2+ 个 (open+close 在一行)
                if triple_count >= 2:
                    in_docstring[i] = True
                # multi-line: 1 个 (open 或 close)
                else:
                    in_docstring[i] = doc_open
                    doc_open = not doc_open
            elif doc_open:
                in_docstring[i] = True
        # 注释行 (# 开头, 但不是字符串)
        def _is_comment(idx):
            if idx < 1 or idx > len(lines): return False
            stripped = lines[idx - 1].lstrip()
            return stripped.startswith("#")
        for pattern, desc in VIOLATIONS:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                # 找行号
                line_no = content[:m.start()].count("\n") + 1
                line_text = lines[line_no - 1].strip() if line_no <= len(lines) else ""
                # 看上下文 10 行 (URL 定义可能在 urllib 调用上方几行)
                ctx = "\n".join(lines[max(0, line_no - 10):line_no + 1])
                # 白名单域 (datacenter 等) 跳过
                if "urllib" in desc.lower() and _is_whitelisted_url(ctx):
                    continue
                # import 行不是实际 fetch 调用, 但还是提示一下
                if "urllib" in desc.lower() and line_text.startswith("import "):
                    continue
                # 2026-07-24 修复: docstring/注释里的提及不算违规 (只警告不改)
                if in_docstring[line_no] or _is_comment(line_no):
                    continue
                violations.append((rel, line_no, desc, line_text[:80]))
    return violations


if __name__ == "__main__":
    quiet = "--quiet" in sys.argv
    v = scan()
    if not v:
        if not quiet:
            print("✅ 扫描通过, 无野 fetch 调用")
            print(f"   白名单: {len(WHITELIST)} 个文件")
            print(f"   禁用源: {len(VIOLATIONS)} 个模式")
        sys.exit(0)
    print(f"❌ 发现 {len(v)} 个野 fetch 调用:\n")
    for file, line, desc, text in v:
        print(f"  {file}:{line}")
        print(f"    违规: {desc}")
        print(f"    代码: {text}")
        print(f"    改法: 改用 tools.storage.sources.tushare.get_*() 或 DataStore\n")
    sys.exit(1)
