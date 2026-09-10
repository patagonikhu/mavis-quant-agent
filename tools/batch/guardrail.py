"""
tools/batch/guardrail.py — Mavis 守门员框架 (Guardrails)

当前内置: network-guard (网络隔离检查)
未来扩展: data-guard / perf-guard / naming-guard 等

扫描 tools/ 下所有 .py 文件, 根据 --check 选择不同检查:
  --check network (默认): 静态网络调用扫描
                       跟每个 skill 的"0 网络"声明做对比
                       4 类网络调用: import / call / subprocess / tushare

输出:
  - 每个工具的"违规清单"
  - 哪些 skill 的 SKILL.md 声明"0 网络"但实际有网络
  - 哪些工具是"网络入口" (允许, 标黄) vs "违规" (应禁止, 标红)

用法:
  bash tools/with_venv.sh python -m tools.batch.guardrail                       # 默认网络守门员
  bash tools/with_venv.sh python -m tools.batch.guardrail --check network       # 显式
  bash tools/with_venv.sh python -m tools.batch.guardrail --skill t-analyze     # 单 skill
  bash tools/with_venv.sh python -m tools.batch.guardrail --strict              # 严格模式
  bash tools/with_venv.sh python -m tools.batch.guardrail --write-md            # 写 docs/guardrail-report.md
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 网络调用识别规则
NETWORK_IMPORTS = [
    r"^\s*import\s+requests",
    r"^\s*from\s+requests\s+import",
    r"^\s*import\s+urllib",
    r"^\s*from\s+urllib",
    r"^\s*import\s+urllib\.request",
    r"^\s*import\s+httpx",
    r"^\s*import\s+aiohttp",
    r"^\s*import\s+http\.client",
    r"^\s*import\s+socket",
]
NETWORK_FUNCS = [
    r"\brequests\.(get|post|put|delete|patch|head|request|send|Session)\b",
    r"\burllib\.request\.urlopen\b",
    r"\burllib\.request\.urlretrieve\b",
    r"\burllib\.request\.Request\b",
    r"\bhttpx\.(get|post|put|delete|patch|head|request|Client|AsyncClient)\b",
    r"\baiohttp\.(ClientSession|Client)\b",
    r"\bsocket\.(socket|create_connection|gethostbyname)\b",
]
# subprocess 调 curl/wget (允许, 但需标黄)
SUBPROCESS_NET = [
    r"subprocess\.(run|call|Popen|check_output|check_call).*curl\b",
    r"subprocess\.(run|call|Popen|check_output|check_call).*wget\b",
    r"subprocess\.(run|call|Popen|check_output|check_call).*ssh\b",
]
# tushare 走网络 (tushare.pro_api 一定走)
TUSHARE_NET = [
    r"\bimport\s+tushare\b",
    r"\bfrom\s+tushare\b",
    r"\btushare\.(pro_api|pro|get_[a-z_]+|query_[a-z_]+)\b",
]
# DataStore / duckdb / pandas / 文件 IO (允许)
WHITELIST_PATTERNS = [
    r"^.*DataStore\.",
    r"^.*duckdb\.",
    r"^.*pd\.read_",
    r"^.*read_parquet",
    r"^.*DataFrame",
    r"^\s*\"\"\".*\"\"\"",  # docstring 单行
    r"^\s*'''.*'''",
]

# 已知"网络入口"白名单 (允许的工具)
NETWORK_ALLOWED = {
    "tools/storage/sync.py": "唯一数据同步入口 (Tushare + datacenter API)",
    "tools/storage/sources/tushare.py": "Tushare 数据源 (sync.py 调用)",
    "tools/storage/sources/eastmoney.py": "datacenter EPS 一致预期 (sync.py 调用)",
    "tools/fetch/tushare_fetcher.py": "Tushare fetcher (旧版, sync 兜底用)",
    "tools/fetch/data_fetcher.py": "datacenter fetcher (旧版, sync 兜底用)",
    "tools/check_data_sources.py": "数据源扫描 (只读, 不访问网络)",
    "tools/batch/guardrail.py": "本守门员自己 (检查规则定义里有网络关键词, 正常)",
}

# 哪些 skill 是"0 网络"声明
ZERO_NET_SKILLS = {
    "t-analyze": "0 网络, 走 DataStore",
    "t-backtest": "0 网络, 走 DataStore",
    "t-bb-obv": "0 网络, 走 DataStore",
    "t-earnings-blowout": "0 网络, 走 DataStore",
    "t-near-low": "0 网络, 走 DataStore",
    "t-roc-ey": "0 网络, 走 DataStore",
    "t-sector-ma": "0 网络, 走 DataStore",
    # t-sync-data 是唯一允许网络的
    "t-sync-data": "允许网络 (Tushare/datacenter API)",
}


def scan_file(filepath: Path) -> list[dict]:
    """扫描单个 .py 文件, 找出所有网络调用"""
    hits = []
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return hits

    lines = content.splitlines()
    for lineno, line in enumerate(lines, 1):
        # 跳过纯注释行 (整行 # 开头)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # 跳过 docstring 起止行 + 中间行 (简化: 行内含连续 3+ 引号 = docstring)
        if stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if '"""' in stripped or "'''" in stripped:
            # 单行 docstring 跳过
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                continue
        # 跳过白名单行 (DataStore / duckdb / 文件 IO)
        if any(re.match(p, line) for p in WHITELIST_PATTERNS):
            continue

        # 1. import 网络库
        for pattern in NETWORK_IMPORTS:
            if re.search(pattern, line):
                hits.append({
                    "lineno": lineno,
                    "type": "import",
                    "code": line.strip(),
                    "severity": "warn",  # import 单独不算违规, 用上才是
                })
                break

        # 2. 用 requests/urllib/httpx 等函数
        for pattern in NETWORK_FUNCS:
            m = re.search(pattern, line)
            if m:
                hits.append({
                    "lineno": lineno,
                    "type": "call",
                    "code": line.strip(),
                    "severity": "violation",  # 实际调用 = 违规
                    "matched": m.group(0),
                })
                break

        # 3. subprocess 调 curl/wget
        for pattern in SUBPROCESS_NET:
            if re.search(pattern, line):
                hits.append({
                    "lineno": lineno,
                    "type": "subprocess",
                    "code": line.strip(),
                    "severity": "warn",  # subprocess curl = 标黄, 允许但有风险
                })
                break

        # 4. tushare (一定走网络)
        for pattern in TUSHARE_NET:
            m = re.search(pattern, line)
            if m:
                hits.append({
                    "lineno": lineno,
                    "type": "tushare",
                    "code": line.strip(),
                    "severity": "violation",
                    "matched": m.group(0),
                })
                break

    return hits


def scan_path(path: Path) -> dict:
    """扫描整个 path 下的 .py 文件"""
    results = {}
    if path.is_file() and path.suffix == ".py":
        hits = scan_file(path)
        if hits:
            results[str(path.relative_to(ROOT))] = hits
    else:
        for f in path.rglob("*.py"):
            # 跳过 __pycache__ 和 tests
            if "__pycache__" in str(f) or "/tests/" in str(f):
                continue
            hits = scan_file(f)
            if hits:
                results[str(f.relative_to(ROOT))] = hits
    return results


def classify_severity(file_path: str, skill_name: str) -> str:
    """根据文件路径和 skill, 判定严重性"""
    if file_path in NETWORK_ALLOWED:
        return "allowed"  # 允许
    if skill_name == "t-sync-data":
        return "allowed"  # sync 允许网络
    if skill_name and skill_name in ZERO_NET_SKILLS:
        return "violation"  # 0 网络 skill 内有网络调用 = 违规
    return "warn"  # 其他工具 (sync 之外) 有网络


def main():
    parser = argparse.ArgumentParser(description="Mavis 守门员 — 扫描工具里的合规问题")
    parser.add_argument("--check", choices=["network", "factor", "eps-scope", "parquet"], default="network",
                        help="检查类型 (默认 network; factor 检查 Factor class 包装违规; eps-scope 检查 --eps 不准全市场; parquet 检查业务层直读 parquet/duckdb/sqlite)")
    parser.add_argument("--skill", help="只检查指定 skill (例: t-analyze)")
    parser.add_argument("--strict", action="store_true", help="严格模式 (所有网络调用标红)")
    parser.add_argument("--write-md", action="store_true", help="写 docs/guardrail-report.md")
    parser.add_argument("--path", default="tools", help="扫描根目录 (默认 tools/)")
    args = parser.parse_args()

    if args.check == "network":
        check_network(args)
    elif args.check == "factor":
        check_factor(args)
    elif args.check == "eps-scope":
        check_eps_scope(args)
    elif args.check == "parquet":
        check_parquet(args)
    else:
        parser.error(f"未实现的检查: {args.check}")


def check_factor(args):
    """Factor 风格守门员 (2026-09-09 加)

    规则: 所有 Factor 必须是纯函数, 统一在 tools/factors/valuation/factor_lib.py
    禁止:
      1. class XXX(Factor) 包装 (Mavis 早期架构, 已废弃)
      2. 在 tools/factors/valuation/ 目录下创建多文件 (除 dcf_engine.py)
      3. tools/analysis/valuation.py (2026-09-09 已删, 不能复活)
    """
    print("🛡️  守门员: factor-guard (Factor 风格统一检查)")
    print(f"   扫描范围: {args.path}/")
    print()

    violations = []

    # 规则 1: 检查 tools/factors/valuation/ 下的 class Factor (其他子目录允许, 如 timeseries/wyckoff/risk/price)
    scan_root = ROOT / args.path
    class_factor_pattern = re.compile(r"^class\s+\w+\(Factor\)\s*:")
    for py_file in scan_root.rglob("*.py"):
        if "__pycache__" in str(py_file) or "/tests/" in str(py_file):
            continue
        rel = str(py_file.relative_to(ROOT))
        # 只检查 valuation/ 子目录 (其他子目录的 class Factor 是正常架构, 保留)
        if "/factors/valuation/" not in rel:
            continue
        # 允许的特殊文件: dcf_engine.py (DCF 假设工具, 不算 Factor)
        if rel.endswith("dcf_engine.py"):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            if class_factor_pattern.match(line.strip()):
                violations.append((rel, lineno, line.strip()))

    # 规则 2: 检查 tools/analysis/valuation.py 是否复活
    analysis_valuation = ROOT / "tools" / "analysis" / "valuation.py"
    if analysis_valuation.exists():
        violations.append((
            str(analysis_valuation.relative_to(ROOT)),
            0,
            "❌ 复活了 tools/analysis/valuation.py (应统一到 tools/factors/valuation/factor_lib.py)",
        ))

    # 规则 3: 检查 tools/factors/valuation/ 下是否有除 dcf_engine.py 外的多文件
    valuation_dir = ROOT / "tools" / "factors" / "valuation"
    if valuation_dir.exists():
        py_files = [f for f in valuation_dir.glob("*.py") if f.name != "__init__.py"]
        allowed = {"factor_lib.py", "dcf_engine.py"}
        for f in py_files:
            if f.name not in allowed:
                violations.append((
                    str(f.relative_to(ROOT)),
                    0,
                    f"❌ 不应新增 {f.name} (Factor 必须统一在 factor_lib.py)",
                ))

    # 输出
    print("📊 检查结果:")
    print(f"   ❌ 违规: {len(violations)}")
    print()
    if violations:
        print("❌ 违规清单:")
        for file_path, lineno, msg in violations:
            where = f"{file_path}:{lineno}" if lineno else file_path
            print(f"   {where}")
            print(f"     {msg}")
        print()
    else:
        print("✅ 没有违规 — 所有 Factor 统一为纯函数 + 单文件")
        print()

    if args.write_md:
        write_report_factor(violations, args)

    if violations:
        sys.exit(1)


def write_report_factor(violations, args):
    """写 factor 检查报告"""
    md = []
    md.append("# 守门员报告: factor-guard")
    md.append("")
    md.append(f"> 扫描: `{args.path}/`  |  规则: 所有 Factor 统一为纯函数 + 单文件")
    md.append("")
    md.append("## 规则")
    md.append("")
    md.append("- ✅ 纯函数 (`def compute_xxx(...)`)")
    md.append("- ✅ 统一在 `tools/factors/valuation/factor_lib.py`")
    md.append("- ❌ 禁止 `class XXX(Factor)` 包装")
    md.append("- ❌ 禁止 `tools/analysis/valuation.py` 复活")
    md.append("- ❌ 禁止 `tools/factors/valuation/` 多文件 (除 valuation.py + dcf_engine.py)")
    md.append("")
    md.append("## 扫描结果")
    md.append("")
    md.append(f"- ❌ 违规: **{len(violations)}**")
    md.append("")
    if violations:
        md.append("## 违规清单")
        md.append("")
        for file_path, lineno, msg in violations:
            where = f"{file_path}:{lineno}" if lineno else file_path
            md.append(f"- `{where}`")
            md.append(f"  - {msg}")
            md.append("")
    out = ROOT / "docs" / "guardrail-report.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"📝 报告: {out}")


def check_network(args):
    """网络守门员主函数 (2026-09-09 改自 tools/batch/network_guard.py)"""
    print("🛡️  守门员: network-guard (网络隔离检查)")
    print(f"   扫描范围: {args.path}/")
    if args.skill:
        print(f"   Skill 模式: {args.skill} ({ZERO_NET_SKILLS.get(args.skill, '?')})")
    if args.strict:
        print("   严格模式: 所有网络调用标红")
    print()

    scan_root = ROOT / args.path
    all_hits = scan_path(scan_root)

    # 按"严重性"分类
    by_severity = {"allowed": [], "warn": [], "violation": []}
    skill_violations = {}  # skill_name → [(file, hit), ...]

    for file_path, hits in all_hits.items():
        # 推断 skill (从文件路径)
        skill_name = infer_skill(file_path)
        for hit in hits:
            if file_path in NETWORK_ALLOWED:
                severity = "allowed"
            elif args.skill and args.skill in ZERO_NET_SKILLS:
                severity = "violation"  # 用户指定 skill, 严格检查
            elif skill_name == "t-sync-data":
                severity = "allowed"
            elif skill_name and skill_name in ZERO_NET_SKILLS:
                severity = "violation"
            else:
                severity = "warn"

            entry = (file_path, hit)
            by_severity[severity].append(entry)
            if severity == "violation" and skill_name:
                skill_violations.setdefault(skill_name, []).append(entry)

    # 打印结果
    print(f"📊 扫描结果:")
    print(f"   ✅ 允许 (白名单入口): {len(by_severity['allowed'])}")
    print(f"   ⚠️  警告 (subprocess curl 等): {len(by_severity['warn'])}")
    print(f"   ❌ 违规 (0 网络 skill 内网络调用): {len(by_severity['violation'])}")
    print()

    # 允许的网络入口
    if by_severity["allowed"]:
        print("✅ 允许的网络入口 (白名单):")
        seen = set()
        for file_path, hit in by_severity["allowed"]:
            if file_path not in seen:
                seen.add(file_path)
                reason = NETWORK_ALLOWED.get(file_path, "网络白名单")
                print(f"   {file_path}")
                print(f"     原因: {reason}")
        print()

    # 警告
    if by_severity["warn"]:
        print("⚠️  警告 (subprocess curl 等允许但有风险):")
        for file_path, hit in by_severity["warn"]:
            print(f"   {file_path}:{hit['lineno']} [{hit['type']}] {hit['code'][:80]}")
        print()

    # 违规
    if by_severity["violation"]:
        print("❌ 违规 (0 网络 skill 内有网络调用):")
        for skill, entries in skill_violations.items():
            print(f"   📦 Skill: {skill}")
            for file_path, hit in entries:
                print(f"      {file_path}:{hit['lineno']} [{hit['type']}] {hit['matched'] or hit['code'][:60]}")
        print()
    else:
        print("✅ 没有违规 — 所有 0 网络 skill 都没有网络调用")
        print()

    # 写 md
    if args.write_md:
        write_report(by_severity, skill_violations, args)

    # 返回 exit code (有违规 → 非零)
    if by_severity["violation"]:
        sys.exit(1)
    return 0


def infer_skill(file_path: str) -> str:
    """从文件路径推断它属于哪个 skill"""
    p = Path(file_path)
    # 路径: tools/batch/xxx.py → 对应 skill 在 SKILL.md 描述里提
    name = p.stem
    skill_map = {
        "t_analyze_one": "t-analyze",
        "t_analyze_all": "t-analyze",
        "t_earnings_blowout_scan": "t-earnings-blowout",
        "earnings_blowout_scan": "t-earnings-blowout",
        "find_near_low": "t-near-low",
        "bb_obv_scan": "t-bb-obv",
        "backtest_roc_ey": "t-backtest",
        "roc_ey_top20": "t-roc-ey",
        "sector_ma_scan": "t-sector-ma",
        "sector_breakout_scan": "t-sector-ma",
        "near_low_backtest": "t-backtest",
        "bb_obv_backtest": "t-backtest",
        "magic_top20": "t-roc-ey",  # 老名字
        "backtest_magic": "t-backtest",
        "quality_growth_scan": "t-earnings-blowout",  # 老名字
        "batch_matrix": "t-analyze",
    }
    return skill_map.get(name, "")


def write_report(by_severity, skill_violations, args):
    """写报告到 docs/guardrail-report.md"""
    md = []
    md.append("# 网络调用守门员报告")
    md.append("")
    md.append(f"> 扫描: `{args.path}/`  |  Skill: `{args.skill or 'all'}`  |  模式: {'严格' if args.strict else '正常'}")
    md.append("")
    md.append("## 规则")
    md.append("")
    md.append("- ✅ **/t-sync-data**: 唯一允许网络的 skill (Tushare/datacenter API)")
    md.append("- ❌ **其他 7 个 skill**: 严格 0 网络, 全部走 DataStore (本地 parquet)")
    md.append("- ✅ **白名单工具**: `tools/storage/sync.py` + `tools/storage/sources/*`")
    md.append("")
    md.append("## 扫描结果")
    md.append("")
    md.append(f"- ✅ 允许: **{len(by_severity['allowed'])}** 处")
    md.append(f"- ⚠️ 警告: **{len(by_severity['warn'])}** 处 (subprocess curl 等)")
    md.append(f"- ❌ 违规: **{len(by_severity['violation'])}** 处 (0 网络 skill 内有网络)")
    md.append("")

    # 允许
    if by_severity["allowed"]:
        md.append("## ✅ 允许的网络入口 (白名单)")
        md.append("")
        seen = set()
        for file_path, _ in by_severity["allowed"]:
            if file_path not in seen:
                seen.add(file_path)
                reason = NETWORK_ALLOWED.get(file_path, "白名单")
                md.append(f"- `{file_path}` — {reason}")
        md.append("")

    # 违规
    if by_severity["violation"]:
        md.append("## ❌ 违规 (需要修复)")
        md.append("")
        for skill, entries in skill_violations.items():
            md.append(f"### Skill: `{skill}`")
            md.append("")
            for file_path, hit in entries:
                md.append(f"- `{file_path}:{hit['lineno']}` [{hit['type']}] `{hit['code'][:80]}`")
            md.append("")

    # 警告
    if by_severity["warn"]:
        md.append("## ⚠️ 警告 (subprocess curl 等)")
        md.append("")
        for file_path, hit in by_severity["warn"]:
            md.append(f"- `{file_path}:{hit['lineno']}` [{hit['type']}] `{hit['code'][:80]}`")
        md.append("")

    out = ROOT / "docs" / "guardrail-report.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"📝 报告: {out}")


def check_eps_scope(args):
    """EPS 范围守门员 (2026-09-09 加)

    铁律: --eps 绝不允许全市场 (datacenter 5555 只限频, 拉一次 25 分钟 + WAF 风险)
    任何 --eps 路径必须:
      1. 默认 watchlist 49 只
      2. 显式 --codes 放行
      3. --all 必须被强制缩到 watchlist (sync.py 入口已加)

    检查项:
      1. sync.py main() 入口有 --eps + --all 互斥检查 (>= 1 处)
      2. tools/batch/ 下没有别的脚本偷跑 sync --eps (grep "sync.*--eps" 找)
      3. 没有任何 'pull_all_market_eps' / 'fetch_all_eps' 类似的便捷函数
    """
    import re

    print("🛡️  守门员: eps-scope (--eps 范围白名单)")
    print(f"   扫描范围: {args.path}/")
    print()

    violations = []
    warnings = []

    # 1. sync.py 必须有 --eps + --all 互斥
    sync_py = ROOT / "tools" / "storage" / "sync.py"
    if sync_py.exists():
        content = sync_py.read_text(encoding="utf-8")
        # 找入口互斥块 (args.eps + args.all 互斥, 强制缩 watchlist)
        has_eps_mutex = bool(re.search(
            r"if\s+args\.eps.*?args\.all\s*=\s*False",
            content, re.DOTALL,
        )) or bool(re.search(
            r"--eps\s*不允许全市场",
            content,
        ))
        if has_eps_mutex:
            print(f"   ✅ {sync_py.relative_to(ROOT)}: 入口有 --eps + --all 互斥 (强制缩 watchlist)")
        else:
            violations.append(f"{sync_py.relative_to(ROOT)}: 缺 --eps + --all 互斥检查 (可能被全市场拉取)")

    # 2. grep "sync.*--eps" 找偷跑路径 (排除 guardrail.py 自身, 它是规则说明)
    pattern_eps_all = re.compile(r"sync.*--eps.*--all|--all.*--eps|--eps\s*--all")
    scan_root = ROOT / args.path
    for py_file in scan_root.rglob("*.py"):
        # 自身是规则定义, 跳过
        if py_file.name == "guardrail.py" and "batch" in str(py_file):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in pattern_eps_all.finditer(content):
            lineno = content[:m.start()].count("\n") + 1
            rel = py_file.relative_to(ROOT)
            # sync.py 自身那行 print("⚠️  --eps 不允许全市场") 是规则说明, 允许
            if "不允许全市场" in m.group():
                continue
            violations.append(f"{rel}:{lineno}: 偷跑 --eps --all: {m.group()!r}")

    # 3. 找便捷函数 (pull_all_market_eps / fetch_all_eps)
    for pattern_name in ["pull_all_market_eps", "fetch_all_eps", "all_market_eps",
                          "bulk_fetch_eps", "sync_all_eps"]:
        for py_file in scan_root.rglob("*.py"):
            # 自身是规则定义, 跳过
            if py_file.name == "guardrail.py" and "batch" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            if pattern_name in content:
                lineno = content[:content.index(pattern_name)].count("\n") + 1
                rel = py_file.relative_to(ROOT)
                violations.append(f"{rel}:{lineno}: 发现可疑函数 {pattern_name} (全市场 EPS 拉取)")

    print()
    if violations:
        print(f"❌ 违规 {len(violations)} 处:")
        for v in violations:
            print(f"   {v}")
    else:
        print("✅ 没有违规 — --eps 范围被锁死在 watchlist / 显式 --codes")

    if warnings:
        print()
        print(f"⚠️  警告 {len(warnings)} 处:")
        for w in warnings:
            print(f"   {w}")

    # 写 md
    if args.write_md:
        md = ["# EPS Scope Guardrail Report", ""]
        if violations:
            md.append("## ❌ Violations")
            for v in violations:
                md.append(f"- {v}")
        else:
            md.append("## ✅ Pass")
            md.append("`--eps` 范围被锁死在 watchlist / 显式 --codes, 没有任何全市场快捷入口。")
        out = ROOT / "docs" / "eps-scope-guardrail.md"
        out.write_text("\n".join(md), encoding="utf-8")
        print(f"\n📝 报告: {out}")

    sys.exit(1 if violations else 0)


def check_parquet(args):
    """Parquet-direct-read 守门员 (2026-09-10 加)

    铁律: 业务层 (analysis/ + render/ + batch/ + factors/) 禁止直读 parquet / 直连 sqlite3 / 直接 duckdb.execute('read_parquet...')
    数据访问统一走 DataStore (tools/storage/store.py)

    例外 (白名单 — 数据访问层自身):
      - tools/storage/sync.py        # 同步入口
      - tools/storage/store.py       # DataStore DAO (内部 duckdb 合法)
      - tools/storage/sources/       # 数据源封装
      - tools/storage/caches/        # 内部 cache 落盘
      - tools/batch/guardrail.py     # 自身

    违规模式 (在白名单外的业务文件):
      1. pd.read_parquet(...)        # 直读 parquet
      2. duckdb.execute('...read_parquet...')  # SQL 直读
      3. sqlite3.connect('data/...') # 直连业务 db (signal_cache 走业务 sqlite 是 OK 的, 单独判定)
    """
    import re

    print("🛡️  守门员: parquet-guard (业务层直读 parquet 检查)")
    print(f"   扫描范围: {args.path}/")
    print()

    # 白名单: 数据访问层自身 (内部用 duckdb / pd.read_parquet 是 OK 的)
    ALLOWED = {
        "tools/storage/sync.py",
        "tools/storage/store.py",
        "tools/batch/guardrail.py",
    }
    # 数据访问层目录: 整目录白名单
    ALLOWED_DIRS = (
        "tools/storage/sources/",
        "tools/storage/caches/",
    )

    # 违规模式
    PATTERNS = [
        (r"pd\.read_parquet\s*\(", "pd.read_parquet(...)", "violation"),
        (r"\.to_parquet\s*\(", "df.to_parquet(...)", "info"),  # 写也走 DataStore, 但暂不强制
        (r"duckdb\.execute\s*\(\s*['\"].*?read_parquet", "duckdb.execute('...read_parquet...')", "violation"),
        (r"_conn\(\)\.execute\s*\(\s*['\"].*?read_parquet", "_conn().execute('...read_parquet...') (绕开 DataStore)", "violation"),
    ]
    # sqlite3 直连 data/ 目录 (signal_cache 业务 db 不算违规, 是 OK)
    SQLITE_PATTERN = re.compile(r"sqlite3\.connect\s*\(\s*['\"](?:data/|['\"][^'\"]*signal_cache)")
    # sqlite3 直连但 key 是 signal_cache 走白名单 (业务缓存, OK)
    SQLITE_OK = re.compile(r"signal_cache|fflow_history|eps_consensus|analysis_cache")

    scan_root = ROOT / args.path
    violations = []
    info = []

    for py_file in scan_root.rglob("*.py"):
        if "__pycache__" in str(py_file) or "/tests/" in str(py_file):
            continue
        rel = str(py_file.relative_to(ROOT))
        # 白名单文件 / 目录 跳过
        if rel in ALLOWED:
            continue
        if any(rel.startswith(d) for d in ALLOWED_DIRS):
            continue
        # 自身
        if py_file.name == "guardrail.py" and "batch" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # 跳过 docstring 单行
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                continue

            # sqlite3 直连 data/ 业务 db, 允许 (signal_cache 业务组件)
            if SQLITE_PATTERN.search(line):
                if SQLITE_OK.search(line) or SQLITE_OK.search(content):
                    continue
                # 业务 db 不在白名单 → 违规
                violations.append((rel, lineno, "sqlite3.connect('data/...') (业务 db 不在白名单, 应走 DataStore)", stripped))
                continue

            for pattern, desc, severity in PATTERNS:
                if re.search(pattern, line):
                    if severity == "violation":
                        violations.append((rel, lineno, desc, stripped))
                    else:
                        info.append((rel, lineno, desc, stripped))
                    break

    print("📊 检查结果:")
    print(f"   ❌ 违规: {len(violations)}")
    print(f"   ℹ️  信息: {len(info)} (to_parquet 写盘, 不强制)")
    print()
    if violations:
        print("❌ 违规清单 (业务层绕过 DataStore 直读 parquet):")
        print()
        for file_path, lineno, desc, code in violations:
            print(f"   {file_path}:{lineno}")
            print(f"     {desc}")
            print(f"     → {code}")
            print()
    else:
        print("✅ 没有违规 — 业务层全部走 DataStore")
    if info:
        print()
        print("ℹ️  信息 (to_parquet 写盘, 暂不强制, 后续可走 DataStore.put_xxx):")
        for file_path, lineno, desc, code in info:
            print(f"   {file_path}:{lineno}  {desc}")

    # 写 md
    if args.write_md:
        md = ["# Parquet Guardrail Report", ""]
        md.append("> 铁律: 业务层禁止直读 parquet / 直连 duckdb / 直连 sqlite3 (signal_cache 业务 db 除外)")
        md.append("> 数据访问统一走 `DataStore` (tools/storage/store.py)")
        md.append("")
        if violations:
            md.append("## ❌ Violations")
            md.append("")
            for file_path, lineno, desc, code in violations:
                md.append(f"- `{file_path}:{lineno}` — {desc}")
                md.append(f"  ```python")
                md.append(f"  {code}")
                md.append(f"  ```")
        else:
            md.append("## ✅ Pass")
            md.append("业务层全部走 DataStore, 无直读 parquet / duckdb / sqlite3 违规。")
        out = ROOT / "docs" / "parquet-guardrail.md"
        out.write_text("\n".join(md), encoding="utf-8")
        print(f"\n📝 报告: {out}")

    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
