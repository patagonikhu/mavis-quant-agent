"""tools/batch/guardrail.py — Mavis 守门员框架 (Guardrails)

用途: 静态扫描 tools/ 下所有 .py 文件, 4 个内置 check 找出合规违规:
       1) network     (默认) — 网络隔离检查 (跟每个 skill "0 网络"声明做对比)
       2) factor      — Factor class 包装违规 (通用 factor 必须纯函数 + 估值单文件)
       3) parquet    — 业务层禁止直读 parquet/duckdb/sqlite (除 signal_cache)
       4) safe-fs    — 禁止直接删用户数据 (unlink/rmtree) + 覆盖写配置 (AST 检测)

2026-11-04 删: eps-scope check (EPS 机构一致预期整档已删, 规则失去意义)

输出:
  - 每个工具的"违规清单"
  - 哪些 skill 的 SKILL.md 声明"0 网络"但实际有网络
  - 哪些工具是"网络入口"(允许, 标黄) vs "违规"(应禁止, 标红)

数据特性:
  - 0 网络, 纯静态扫描 (grep/regex)
  - 扫描根目录默认 tools/ (--path 可调)
  - --strict 严格模式 (所有网络调用标红)

用法:
  bash tools/with_venv.sh python -m tools.batch.guardrail                       # 默认网络守门员
  bash tools/with_venv.sh python -m tools.batch.guardrail --check parquet       # 业务层直读 parquet/duckdb
  bash tools/with_venv.sh python -m tools.batch.guardrail --check factor        # Factor class 包装违规
  bash tools/with_venv.sh python -m tools.batch.guardrail --check safe-fs       # 删用户数据/覆盖写配置
  bash tools/with_venv.sh python -m tools.batch.guardrail --check cache-writer  # 行业威科夫 cache 只有 /t-sync-data 能写
  bash tools/with_venv.sh python -m tools.batch.guardrail --skill t-analyze     # 单 skill
  bash tools/with_venv.sh python -m tools.batch.guardrail --strict --write-md   # 严格 + 写 docs/guardrail-report.md
"""
import argparse
import ast
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
#
# ⚠️ 2026-11-12: 原来的正则只认 `import tushare` / `from tushare import`,
#    **漏过项目里最常见的两种写法** ——
#      `from .sources.tushare import get_daily`          (相对路径, store.py 里 7 处)
#      `from tools.storage.sources.tushare import ...`   (绝对路径)
#    今天没出事纯粹是因为调用链收敛 (那 7 处只有 sync.py 调), 不是守门员抓出来的。
#    → 现在改用 AST 精确识别 (见 _ast_tushare_lines), 正则只留「不带 import 的属性调用」兜底。
TUSHARE_NET = [
    r"\btushare\.(pro_api|pro|get_[a-z_]+|query_[a-z_]+)\b",
]

# 哪些文件的「sync_* 函数内」允许出现 tushare (sync 路径的写盘原语)
# 2026-11-12 加: store.py 里的 sync_incremental / sync_stock_basic / sync_financials 等
#   必须在函数内部 `from .sources.tushare import ...`, 否则拉不到数据。
#   只放行**函数名含 sync** 的那几处, 同一个文件的其它函数出现 tushare = 越界。
SYNC_SCOPE_FILES = {
    "tools/storage/store.py": "DataStore 的 sync_* 写盘原语 (只允许 sync_* 函数内拉网络)",
}
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
# 2026-11-06 清理: 删 3 条**文件已不存在**的死路径 + 1 条已纯计算的
#   - tools/fetch/tushare_fetcher.py / tools/fetch/data_fetcher.py / tools/check_data_sources.py → 文件没了
#   - tools/storage/sources/eastmoney.py → 2026-11-02 起是**纯计算模块** (0 网络, 名字是历史遗留)
#   - sync.py 的 datacenter API → 2026-11-04 EPS 一致预期整档已删, 现在只走 Tushare
NETWORK_ALLOWED = {
    "tools/storage/sync.py": "唯一数据同步入口 (Tushare)",
    "tools/storage/sources/tushare.py": "Tushare 数据源 (sync.py 调用)",
    "tools/batch/guardrail.py": "本守门员自己 (检查规则定义里有网络关键词, 正常)",
}

# 哪些 skill 是"0 网络"声明 (2026-10-20 修: 跟当前真实 skill 列表对齐)
# 注意: 改名 / 删名的 skill 历史上叫过 t-bb-obv / t-earnings-blowout / t-sector-ma,
#       那些已废弃, 不要在这里复活
ZERO_NET_SKILLS = {
    "t-analyze": "0 网络, 走 DataStore",
    "t-backtest": "0 网络, 走 DataStore",
    "t-factor-5y": "0 网络, 走 DataStore",
    "t-finance-earnings-blowout": "0 网络, 走 DataStore",
    "t-wyckoff-a2m": "0 网络, 走 DataStore",
    # 2026-11-05 加: ETF 版 A→M (数据走 DataStore.get_etf_basic / get_etf_klines)
    "t-etf-wyckoff-a2m": "0 网络, 走 DataStore",
    # t-sync-data 是唯一允许网络的
    "t-sync-data": "允许网络 (Tushare/datacenter API)",
}


def _ast_tushare_lines(content: str) -> tuple[set[int], dict[int, str]]:
    r"""AST 精确识别 tushare 相关行。

    Returns:
        (tainted_lines, enclosing_func)
          tainted_lines: 所有涉及 tushare 的行号 (import 行 + 别名调用行)
          enclosing_func: 行号 → 该行所在的最内层函数名 (模块级 = "")

    为什么不用正则 (2026-11-12 修):
      正则 `\bfrom\s+tushare\b` 匹配不到 `from .sources.tushare import ...`
      (相对路径带 `.sources.` 前缀), 也匹配不到 `import tushare as ts` 之后的
      `ts.pro_api(...)` (别名调用)。这两种写法项目里都有, 正则是瞎的。

    AST 能精确识别:
      - `import tushare` / `import tushare as ts`
      - `from tushare import pro_api`
      - `from .sources.tushare import X`        (level=1, module="sources.tushare")
      - `from tools.storage.sources.tushare import X`
      - `from ..storage.sources import tushare`
      - 别名后的 `ts.pro_api(...)` / `ts.get_daily(...)`
    """
    tainted: set[int] = set()
    enclosing: dict[int, str] = {}
    aliases: set[str] = set()

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return tainted, enclosing

    def _is_tushare_mod(name: str) -> bool:
        return name == "tushare" or name.endswith(".tushare")

    # 1) import 语句 → 记行号 + 收集别名
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _is_tushare_mod(a.name):
                    tainted.add(node.lineno)
                    aliases.add(a.asname or a.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _is_tushare_mod(mod):
                tainted.add(node.lineno)
            for a in node.names:
                if _is_tushare_mod(a.name):
                    tainted.add(node.lineno)
                    aliases.add(a.asname or a.name.split(".")[-1])

    # 2) 别名调用 (ts.pro_api / ts.get_daily ...)
    if aliases:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                        and f.value.id in aliases
                        and f.attr.startswith(("pro_api", "pro", "get_", "query_"))):
                    tainted.add(node.lineno)

    # 3) 行号 → 所在函数 (取最内层)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                enclosing[ln] = node.name

    return tainted, enclosing


def scan_file(filepath: Path) -> list[dict]:
    """扫描单个 .py 文件, 找出所有网络调用"""
    hits = []
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return hits

    lines = content.splitlines()
    # 2026-11-12 加: AST 预扫 —— tushare 相关行 + 行号→所在函数
    tushare_lines, enclosing_func = _ast_tushare_lines(content)
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

        # 4. tushare (一定走网络) — AST 精确识别 + 正则兜底
        #    AST 能抓到相对/绝对路径 import 和别名调用 (正则抓不到, 见 _ast_tushare_lines)
        if lineno in tushare_lines:
            hits.append({
                "lineno": lineno,
                "type": "tushare",
                "code": line.strip(),
                "severity": "violation",
                "matched": "tushare",
                "enclosing_func": enclosing_func.get(lineno, ""),
            })
        else:
            for pattern in TUSHARE_NET:
                m = re.search(pattern, line)
                if m:
                    hits.append({
                        "lineno": lineno,
                        "type": "tushare",
                        "code": line.strip(),
                        "severity": "violation",
                        "matched": m.group(0),
                        "enclosing_func": enclosing_func.get(lineno, ""),
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


def main():
    parser = argparse.ArgumentParser(description="Mavis 守门员 — 扫描工具里的合规问题")
    parser.add_argument("--check", choices=["network", "factor", "parquet", "safe-fs", "cache-writer"],
                        default="network",
                        help="检查类型 (默认 network; factor 检查 Factor class 包装违规; parquet 检查业务层直读 parquet/duckdb/sqlite; safe-fs 检查直接删用户数据/覆盖写配置; cache-writer 检查除 /t-sync-data 外没人写行业威科夫 cache)")
    parser.add_argument("--skill", help="只检查指定 skill (例: t-analyze)")
    parser.add_argument("--strict", action="store_true", help="严格模式 (所有网络调用标红)")
    parser.add_argument("--write-md", action="store_true", help="写 docs/guardrail-report.md")
    parser.add_argument("--path", default="tools", help="扫描根目录 (默认 tools/)")
    args = parser.parse_args()

    if args.check == "network":
        check_network(args)
    elif args.check == "factor":
        check_factor(args)
    elif args.check == "parquet":
        check_parquet(args)
    elif args.check == "safe-fs":
        check_safe_fs(args)
    elif args.check == "cache-writer":
        check_cache_writer(args)
    else:
        parser.error(f"未实现的检查: {args.check}")


SAFE_FS_ALLOWED = {
    "tools/storage/safe_fs.py",
    "tools/batch/guardrail.py",
}
SAFE_FS_ALLOWED_DIRS = ("tools/storage/",)


def scan_source_safe_fs(rel, content, sensitive_re=None):
    """AST 检测单个文件里的危险文件操作 (纯函数, 可单测)

    返回 (violations, info) 两个 list, 元素都是 (rel, lineno, msg)。

    为什么不纯 AST 匹配:
      - AST 天然跳过 docstring (它们是 Constant 节点, 不是 Call) → 不会把
        文档里写的 `os.remove(...)` 示例误判成真代码
      - 跨行调用 `os.remove(\n  p\n)` 行级 regex 漏掉, AST 抓得到
      - 变量间接引用 `p = "docs/x.md"` → `os.remove(p)` 需要单独建表关联

    sensitive_re: 匹配敏感路径的 regex, 默认 config/|watchlist|whitelist|docs/
    """
    import re

    if sensitive_re is None:
        sensitive_re = re.compile(r"config/|watchlist|whitelist|docs/")

    if rel in SAFE_FS_ALLOWED:
        return [], []
    if any(rel.startswith(d) for d in SAFE_FS_ALLOWED_DIRS):
        return [], []

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [], []

    # 变量追踪 — 把 "x = 'docs/xxx'" 的赋值跟 "os.remove(x)" 关联起来
    #   (AST Call 节点里看不到变量指向的字符串, 需要单独建表)
    var_sensitive = {}
    var_alias = {}  # 变量 -> 它指向的另一个变量 (q = p), 用于二次间接引用
    for vnode in ast.walk(tree):
        if isinstance(vnode, ast.Assign):
            # 赋值右边的字符串常量
            val_str = ""
            for sub in ast.walk(vnode.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    val_str = sub.value
            # 赋值右边是另一个变量 → 别名 (q = p)
            rhs_names = [
                sub.id for sub in ast.walk(vnode.value)
                if isinstance(sub, ast.Name)
            ]
            for tgt in vnode.targets:
                if not isinstance(tgt, ast.Name):
                    continue
                if val_str and sensitive_re.search(val_str):
                    var_sensitive[tgt.id] = val_str
                elif rhs_names:
                    var_alias[tgt.id] = rhs_names[0]
        elif isinstance(vnode, ast.AnnAssign) and vnode.value is not None:
            val_str = ""
            for sub in ast.walk(vnode.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    val_str = sub.value
            if val_str and sensitive_re.search(val_str) and isinstance(vnode.target, ast.Name):
                var_sensitive[vnode.target.id] = val_str

    # 别名解析: q = p, p = "docs/x.md" → q 也算敏感 (最多解 5 层防环)
    for _ in range(5):
        changed = False
        for alias, target in list(var_alias.items()):
            if alias in var_sensitive:
                continue
            if target in var_sensitive:
                var_sensitive[alias] = var_sensitive[target]
                changed = True
        if not changed:
            break

    violations = []
    info = []
    src_lines = content.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # 定位被调用的名字: f(...) / os.remove(...) / shutil.rmtree(...)
        fname = ""
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr

        # 该调用里出现的所有字符串常量 (路径可能不在同一行)
        str_args = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                str_args.append(sub.value)
        # 变量名里可能带路径 (如 WATCHLIST_PATH) → 也看属性/名字
        ident_args = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                ident_args.append(sub.id)
            elif isinstance(sub, ast.Attribute):
                ident_args.append(sub.attr)

        blob = " ".join(str_args + ident_args)
        # 变量指向敏感路径 → 也算命中 (如 p = "docs/x.md"; os.remove(p))
        if not sensitive_re.search(blob):
            extra = " ".join(var_sensitive[i] for i in ident_args if i in var_sensitive)
            if not extra:
                continue
            blob += " " + extra
        if not sensitive_re.search(blob):
            continue

        line_no = getattr(node, "lineno", 0)
        src_line = src_lines[line_no - 1].strip() if line_no else ""

        if fname in ("unlink", "remove", "rmtree"):
            violations.append((rel, line_no, f"❌ {fname}() 直接删敏感文件: {src_line[:80]}"))
        elif fname == "write_text":
            info.append((rel, line_no, f"⚠️ write_text() 覆盖写敏感文件 (建议 atomic_write_json): {src_line[:80]}"))
        elif fname == "open":
            # open() 只有在写模式 ("w"/"a"/"x"/"+") 才是覆盖写; 只读 "r" 不算
            mode = ""
            for a in node.args[1:2]:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    mode = a.value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if any(m in mode for m in ("w", "a", "x", "+")):
                info.append((rel, line_no, f"⚠️ open({mode!r}) 覆盖写敏感文件 (建议 atomic_write_json): {src_line[:80]}"))

    return violations, info


def check_safe_fs(args):
    """安全文件操作守门员 (2026-11-02 加)

    铁律: 批量脚本 **不许直接删用户数据 / 直接覆盖写用户配置**。
    背景: A→M 脚本曾用 `p.unlink()` 清空 docs/ 下 11 份历史详报,
    而 docs/ 被 .gitignore 排除 → 删了不可恢复 (事故).

    违规模式 (业务层, 白名单外):
      1. .unlink() / os.remove() / shutil.rmtree()  指向 docs/ 或 config/ 下的文件
      2. open(path, "w") / write_text() 直接覆盖 config/ 下的 JSON (持仓配置)

    正确做法:
      - 删除 → tools.storage.safe_fs.safe_delete (移到 ~/.Trash, 永不 unlink)
      - 写配置 → tools.storage.safe_fs.atomic_write_json (原子写 + 写前备份)
      - 或走 DataStore.save_watchlist (已原子写)

    例外 (白名单):
      - tools/storage/safe_fs.py      # 本身就是安全层
      - tools/batch/guardrail.py      # 自身
      - tools/storage/                 # 数据层, parquet/sqlite 写入是正常的
    """
    import re

    print("🛡️  守门员: safe-fs-guard (禁止直接删用户数据 / 覆盖写配置)")
    print(f"   扫描范围: {args.path}/")
    print()

    SENSITIVE = re.compile(r"config/|watchlist|whitelist|docs/")

    violations = []
    info = []

    for py_file in (ROOT / args.path).rglob("*.py"):
        if "__pycache__" in str(py_file) or "/tests/" in str(py_file):
            continue
        rel = str(py_file.relative_to(ROOT))
        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        v, i = scan_source_safe_fs(rel, content, SENSITIVE)
        violations.extend(v)
        info.extend(i)

    print("📊 检查结果:")
    print(f"   ❌ 违规 (直接删用户数据): {len(violations)}")
    print(f"   ⚠️ 警告 (覆盖写配置):     {len(info)}")
    print()
    if violations:
        print("❌ 违规清单:")
        for file_path, lineno, msg in violations:
            print(f"   {file_path}:{lineno}")
            print(f"     {msg}")
        print()
        print("   修法: 用 tools.storage.safe_fs.safe_delete (移回收站, 可恢复)")
        print()
    if info:
        print("⚠️ 警告清单:")
        for file_path, lineno, msg in info:
            print(f"   {file_path}:{lineno}")
            print(f"     {msg}")
        print()
        print("   修法: 用 tools.storage.safe_fs.atomic_write_json (原子写 + 备份)")
        print()

    if not violations and not info:
        print("✅ 没有违规 — 所有用户数据的删除/写入都走 safe_fs 安全层")
        print()

    if violations:
        sys.exit(1)


CACHE_WRITER_ALLOWED = {
    "tools/storage/sync.py",                 # 唯一合法写者 (= /t-sync-data)
    "tools/storage/caches/industry_wyckoff.py",   # cache 模块自身
    "tools/batch/guardrail.py",              # 自身
}


def scan_source_cache_writer(rel, content):
    """检测"非 sync 模块碰行业威科夫 cache 写入口" (AST, 纯函数, 可单测)

    返回 violations 列表, 元素 (rel, lineno, msg)。

    违规模式 (白名单外):
      1. 调用 industry_wyckoff 的写入口 (write_rows / write_rows_for_sync)
      2. 直接引用 cache 目录常量 INDUSTRY_WYCKOFF_DIR (绕过 API 写盘)

    为什么用 AST 而不是 grep:
      docstring 里写"原来这里会 cache.write_rows(rows)"是**说明**, 不是调用 ——
      grep 会误报 (AST 的 docstring 是 Constant 节点, 天然跳过)。
    """
    import ast

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else "")
            if name in ("write_rows", "write_rows_for_sync"):
                violations.append((
                    rel, node.lineno,
                    f"调用行业威科夫 cache 写入口 {name}() —— "
                    "只有 tools/storage/sync.py (/t-sync-data) 能写"))
        elif isinstance(node, ast.Name) and node.id == "INDUSTRY_WYCKOFF_DIR":
            violations.append((
                rel, node.lineno,
                "直接引用行业威科夫 cache 目录常量 —— "
                "只有 tools/storage/sync.py (/t-sync-data) 能写"))
        elif isinstance(node, ast.alias) and (node.name or "").endswith("INDUSTRY_WYCKOFF_DIR"):
            violations.append((
                rel, node.lineno,
                "直接 import 行业威科夫 cache 目录常量 —— "
                "只有 tools/storage/sync.py (/t-sync-data) 能写"))
    return violations


def check_cache_writer(args):
    """行业威科夫 cache 写权限守门员 (2026-11-12 加)

    铁律: `data/cache/industry_wyckoff/` **只有 /t-sync-data 能写**。
    背景: 原来是「查 cache → 算 → 回写」的懒惰写盘, 批量分析时 4 worker 多进程
    会同时写同一年 parquet → 必须上文件锁 (fcntl) + 读改写 + 原子替换。
    写路径收归 sync 之后: 写永远单进程 → 不用锁, 逻辑简单很多;
    读侧 (分析层) 只查表, miss 就内存现算, 不回写。

    违规模式 (白名单外):
      1. 调用 industry_wyckoff 的写入口 (write_rows / write_rows_for_sync)
      2. 直接引用 INDUSTRY_WYCKOFF_DIR (绕过 API 写盘)

    例外 (白名单):
      - tools/storage/sync.py                      # 唯一合法写者
      - tools/storage/caches/industry_wyckoff.py   # cache 模块自身
      - tools/batch/guardrail.py                  # 自身
    """
    print("🛡️  守门员: cache-writer-guard (行业威科夫 cache 只有 /t-sync-data 能写)")
    print(f"   扫描范围: {args.path}/")
    print()

    violations = []

    for py_file in (ROOT / args.path).rglob("*.py"):
        if "__pycache__" in str(py_file) or "/tests/" in str(py_file):
            continue
        rel = str(py_file.relative_to(ROOT))
        if rel in CACHE_WRITER_ALLOWED:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        violations.extend(scan_source_cache_writer(rel, content))

    print("📊 检查结果:")
    print(f"   ❌ 违规 (非 sync 写行业威科夫 cache): {len(violations)}")
    print()

    if violations:
        print("❌ 违规清单:")
        for file_path, lineno, msg in violations:
            print(f"   {file_path}:{lineno}")
            print(f"     {msg}")
        print()
        print("   修法: 写盘交给 /t-sync-data (tools/storage/sync.py);")
        print("         分析侧只读 —— 查不到就在内存现算, 不回写")
        print()

    if not violations:
        print("✅ 没有违规 — 行业威科夫 cache 只有 /t-sync-data 一个写者")
        print()

    if violations:
        sys.exit(1)


def check_factor(args):
    """Factor 风格守门员 (2026-09-09 加)

    规则: 所有 Factor 必须是纯函数, 统一在 tools/factors/valuation/factor_lib.py
    禁止:
      1. class XXX(Factor) 包装 (Mavis 早期架构, 已废弃)
      2. 在 tools/factors/valuation/ 目录下创建多文件 (只允许 factor_lib.py)
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

    # 规则 3: 检查 tools/factors/valuation/ 下是否有除 factor_lib.py 外的多文件
    # 2026-11-02: dcf_engine.py 已删 (DCF 整档删除后它完全没人用)
    valuation_dir = ROOT / "tools" / "factors" / "valuation"
    if valuation_dir.exists():
        py_files = [f for f in valuation_dir.glob("*.py") if f.name != "__init__.py"]
        allowed = {"factor_lib.py"}
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
    md.append("- ❌ 禁止 `tools/factors/valuation/` 多文件 (只允许 factor_lib.py)")
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


def classify_hit(file_path: str, hit: dict, skill_name: str, args) -> str:
    """单条命中的严重性判定 → allowed / warn / violation

    抽成独立函数 (2026-11-12) 是为了能写单元测试 —— 判定逻辑是「网络隔离」
    的最后一道防线, 必须能离线验证, 不能只靠跑一遍看输出。

    tushare 特判 (2026-11-12):
      tushare 只能出现在 sync 路径。判定顺序:
        1) 白名单文件 (sync.py / sources/tushare.py / guardrail.py) → allowed
        2) SYNC_SCOPE_FILES 里**函数名含 sync** 的那几处 → allowed
           (store.py 的 sync_incremental / sync_stock_basic / sync_financials ...)
        3) 其余任何地方出现 tushare → violation (不依赖 skill 推断,
           免得「未登记的 batch 脚本」被判成 warn 放过去)
    """
    if hit["type"] == "tushare" and file_path not in NETWORK_ALLOWED:
        func = hit.get("enclosing_func", "")
        if file_path in SYNC_SCOPE_FILES and "sync" in func:
            return "allowed"
        return "violation"
    if file_path in NETWORK_ALLOWED:
        return "allowed"
    if args.skill and args.skill in ZERO_NET_SKILLS:
        return "violation"  # 用户指定 skill, 严格检查
    if skill_name == "t-sync-data":
        return "allowed"
    if skill_name and skill_name in ZERO_NET_SKILLS:
        return "violation"
    return "warn"


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
            severity = classify_hit(file_path, hit, skill_name, args)

            entry = (file_path, hit)
            by_severity[severity].append(entry)
            # 2026-11-12: 不再因 skill_name 为空就丢掉违规 (tushare 越界判定
            # 已经不依赖 skill 推断, 未登记的模块也得报出来)
            if severity == "violation":
                skill_violations.setdefault(skill_name or "(未登记模块)", []).append(entry)

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
                reason = NETWORK_ALLOWED.get(file_path) or SYNC_SCOPE_FILES.get(file_path, "网络白名单")
                print(f"   {file_path}")
                print(f"     原因: {reason}")
        # sync_* 函数内的 tushare (2026-11-12) 单独列出来, 让人一眼看到是哪些
        sync_scoped = [(f, h) for f, h in by_severity["allowed"]
                       if h["type"] == "tushare" and f in SYNC_SCOPE_FILES]
        if sync_scoped:
            print("   —— 以下为 sync_* 函数内的 tushare (合法, 只放行 sync 路径):")
            for file_path, hit in sync_scoped:
                print(f"      {file_path}:{hit['lineno']}  {hit.get('enclosing_func') or '(模块级)'}")
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
        "sector_ma_scan": "t-sector-ma",
        "sector_breakout_scan": "t-sector-ma",
        "backtest_magic": "t-backtest",
        "quality_growth_scan": "t-earnings-blowout",  # 老名字
        "batch_matrix": "t-analyze",
        # 2026-11-05 加: A→M 两个扫描器 (之前漏了, 0 网络声明没被强制)
        "wyckoff_a2m_scan": "t-wyckoff-a2m",
        "etf_wyckoff_a2m_scan": "t-etf-wyckoff-a2m",
        "a2m_report_common": "t-wyckoff-a2m",  # 公共模块 (股票+ETF 共用)
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
    md.append("- ✅ **sync 原语**: `tools/storage/store.py` 的 `sync_*` 函数内可拉 tushare "
              "(同一文件其它函数出现 tushare = 违规)")
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
    SQLITE_OK = re.compile(r"signal_cache|analysis_cache")

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
