"""
依赖检查 + 自动安装 (2026-07-24 固化)

目的: 解决 "No module named 'tushare'" 反复忘装问题
用法: 在 ensure_fresh / sync_watchlist_fresh / render_report 等数据入口的开头 import 一次
      from tools.check_deps import ensure
      ensure()
效果: 检查 tushare 是否装, 缺则 pip install (静默)

设计原则:
  - 幂等: 已装就跳过 (不重复 pip install)
  - 静默: -q 模式, 不刷屏
  - 容错: pip install 失败不 raise, 只 warning (避免阻塞主流程)
  - 不破坏虚拟环境: 直接装到当前 python3 的 site-packages

Memory: 见 docs/AGENT_MEMORY.md "Tushare 接入 (已完成 2026-07-24)"
"""
import subprocess
import sys

_REQUIRED = ['tushare']
_INSTALLED_CACHE = None  # 进程内只检查一次


def _have(pkg: str) -> bool:
    try:
        __import__(pkg)
        return True
    except Exception:
        # 不只 ImportError, 还包括 SyntaxError / TypeError (包版本和 Python 不兼容)
        return False


def ensure(verbose: bool = False) -> bool:
    """
    确保 tushare 已装. 返回 True = 全部就绪.
    已就绪时几乎零开销 (只跑一次 import check).
    """
    global _INSTALLED_CACHE
    if _INSTALLED_CACHE is not None:
        return _INSTALLED_CACHE

    missing = [p for p in _REQUIRED if not _have(p)]
    if not missing:
        _INSTALLED_CACHE = True
        return True

    if verbose:
        print(f"⚠️  缺失依赖: {missing}, 自动安装中...", file=sys.stderr)

    try:
        cmd = [sys.executable, '-m', 'pip', 'install', '-q'] + missing
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode == 0:
            if verbose:
                print(f"✅ 装好了: {missing}", file=sys.stderr)
            _INSTALLED_CACHE = True
            return True
        else:
            err = r.stderr.decode('utf-8', 'ignore')[-300:]
            print(f"❌ pip install 失败: {err}", file=sys.stderr)
            _INSTALLED_CACHE = False
            return False
    except Exception as e:
        print(f"❌ check_deps 出错: {e}", file=sys.stderr)
        _INSTALLED_CACHE = False
        return False


if __name__ == '__main__':
    ok = ensure(verbose=True)
    print('READY' if ok else 'FAILED')
    sys.exit(0 if ok else 1)
