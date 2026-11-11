"""tools/batch/guardrail.py —— 网络隔离 (tushare 越界判定) 单元测试

2026-11-12 加: 守门员的 tushare 正则原本只认 `import tushare` / `from tushare import`,
漏过项目里最常见的两种写法:
  - `from .sources.tushare import get_daily`          (相对路径, store.py 里 7 处)
  - `from tools.storage.sources.tushare import ...`   (绝对路径)
今天没出事只是因为调用链收敛, 不是守门员抓出来的 → 改成 AST 精确识别 + 补测试。

这套测试守的是:
  1. AST 能抓到各种写法的 tushare 引用 (相对/绝对/别名/函数内)
  2. docstring 里提到 tushare 不能误报
  3. store.py 里的 tushare import 必须**全部**在 sync_* 函数内 (越界即失败)
  4. 业务层 (analysis/render/batch) 一个 tushare 都不能有
"""
import argparse
from pathlib import Path

from tools.batch.guardrail import (
    NETWORK_ALLOWED,
    SYNC_SCOPE_FILES,
    _ast_tushare_lines,
    classify_hit,
    scan_path,
)

ROOT = Path(__file__).resolve().parents[2]


def _args(skill=None):
    return argparse.Namespace(skill=skill, strict=False, path="tools")


# ----- 1. AST 识别各种写法 -----

def test_ast_catches_relative_import():
    """`from .sources.tushare import X` (相对路径) —— 原正则漏掉的那个"""
    src = "from .sources.tushare import get_daily_by_date\n"
    tainted, _ = _ast_tushare_lines(src)
    assert tainted == {1}


def test_ast_catches_absolute_import():
    """`from tools.storage.sources.tushare import X` (绝对路径)"""
    src = "from tools.storage.sources.tushare import get_daily\n"
    tainted, _ = _ast_tushare_lines(src)
    assert tainted == {1}


def test_ast_catches_relative_from_package():
    """`from ..storage.sources import tushare` (从包导入模块本身)"""
    src = "from ..storage.sources import tushare\n"
    tainted, _ = _ast_tushare_lines(src)
    assert tainted == {1}


def test_ast_catches_alias_call():
    """`import tushare as ts` 之后的 `ts.pro_api(...)` —— 正则抓不到别名调用"""
    src = "import tushare as ts\n\ndef f():\n    return ts.pro_api('token')\n"
    tainted, _ = _ast_tushare_lines(src)
    assert tainted == {1, 4}


def test_ast_tracks_enclosing_function():
    """行号 → 所在函数名 (判定「sync_* 函数内」的依据)"""
    src = "def sync_thing():\n    from .sources.tushare import get_daily\n"
    tainted, enclosing = _ast_tushare_lines(src)
    assert tainted == {2}
    assert enclosing[2] == "sync_thing"


def test_ast_no_false_positive_on_docstring():
    """docstring 里提到 tushare 不能算命中 (否则 sources/tushare.py 会自己告自己)"""
    src = '"""本模块封装 tushare 接口, from .sources.tushare import ...\n"""\nX = 1\n'
    tainted, _ = _ast_tushare_lines(src)
    assert tainted == set()


def test_ast_ignores_unrelated_module():
    """非 tushare 的相对 import 不该被误报"""
    src = "from .sources.eastmoney import compute_indicators\n"
    tainted, _ = _ast_tushare_lines(src)
    assert tainted == set()


# ----- 2. 严重性判定 -----

def test_tushare_outside_sync_is_violation():
    """任何非 sync 路径的 tushare = violation (不依赖 skill 推断)"""
    hit = {"type": "tushare", "enclosing_func": ""}
    assert classify_hit("tools/batch/xxx.py", hit, "", _args()) == "violation"


def test_tushare_in_sync_func_of_store_is_allowed():
    """store.py 的 sync_* 函数内 → allowed"""
    hit = {"type": "tushare", "enclosing_func": "sync_incremental"}
    assert classify_hit("tools/storage/store.py", hit, "", _args()) == "allowed"


def test_tushare_in_non_sync_func_of_store_is_violation():
    """store.py 的**非** sync 函数内出现 tushare = violation (同步原语漏到读路径)"""
    hit = {"type": "tushare", "enclosing_func": "get_kline"}
    assert classify_hit("tools/storage/store.py", hit, "", _args()) == "violation"


def test_whitelisted_files_still_allowed():
    """白名单文件 (sync.py / sources/tushare.py) 不受影响"""
    hit = {"type": "tushare", "enclosing_func": ""}
    for f in NETWORK_ALLOWED:
        assert classify_hit(f, hit, "", _args()) == "allowed"


def test_sync_scope_files_defined():
    """SYNC_SCOPE_FILES 必须指向真实的 store.py (路径写错 = 白名单失效)"""
    assert "tools/storage/store.py" in SYNC_SCOPE_FILES
    assert (ROOT / "tools/storage/store.py").exists()


# ----- 3. 真实仓库状态 -----

def test_store_tushare_imports_all_inside_sync_funcs():
    """store.py 里的 tushare import 必须**全部**落在 sync_* 函数内。

    这是本条规则的实况锁: 有人在 store.py 的读路径里加一行 tushare import,
    这个测试立刻失败 —— 那正是今天原正则漏掉的那类越界。
    """
    path = ROOT / "tools/storage/store.py"
    content = path.read_text(encoding="utf-8")
    tainted, enclosing = _ast_tushare_lines(content)
    assert tainted, "store.py 应该有 sync_* 原语的 tushare import (若全没了请同步改规则)"
    for ln in tainted:
        func = enclosing.get(ln, "")
        assert "sync" in func, (
            f"store.py:{ln} 的 tushare import 不在 sync_* 函数内 (所在: {func or '模块级'}) "
            "—— 读路径不许拉网络"
        )


def test_business_layer_has_no_tushare():
    """业务层 (analysis / render / batch) 一个 tushare 都不能有"""
    for sub in ("tools/analysis", "tools/render", "tools/batch"):
        res = scan_path(ROOT / sub)
        for file_path, hits in res.items():
            tushare_hits = [h for h in hits if h["type"] == "tushare"]
            assert not tushare_hits, (
                f"{file_path} 出现 tushare 引用 {tushare_hits} —— "
                "业务层必须 0 网络, 只走 DataStore"
            )
