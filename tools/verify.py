"""
tools/verify.py — 分层验证入口 (2026-11-10 加)

背景
----
之前每次改完都跑「全市场扫描 + 全量详报」, 一次改动会话烧掉 8 分钟纯验证。
实测各项成本后发现 —— 贵的不是检查本身, 是**重复跑全量**:

    19 脚本 import   单进程 0.55s   (之前用 19 次独立进程 = 57s)
    pytest           8.5s
    guardrail 4 项    0.65s
    py_compile       0.27s
    ──────────────────────────────
    L0+L1+L2 合计     ~10s   ← 覆盖 80% 的问题
    全市场扫描        112s   ← 只有改口径才需要
    全量详报          83s   ← 只有改落盘结构才需要

所以: **按改动影响面分层, 每层只跑够用的那一档**。

用法
----
    bash tools/with_venv.sh python -m tools.verify          # = --fast
    bash tools/with_venv.sh python -m tools.verify --fast   # L0+L1+L2 (~12s)
    bash tools/with_venv.sh python -m tools.verify --full   # 上面 + L3 全量扫描 (~2min)

预算表 (改完跑哪一层)
----------------------
| 改动类型                        | 跑哪层              | 成本 |
|---------------------------------|---------------------|------|
| 文案 / docstring / 注释         | L0                  | 1s   |
| 共享 helper / 渲染器 / 过滤器    | L0+L1               | 10s  |
| 过滤链口径 (候选池 / 门槛)       | L0+L1+**L3 扫描**   | 2min |
| 详报落盘 / 目录结构             | L0+L1+**L3 全量**   | 3.5min |
| 同一特性的连续改动              | 合并到最后跑 1 次   | —    |

铁律: **全量验证一个改动会话只跑一次, 放最后**。中间步骤跑 L0/L1 就够。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 批量 import 检查: 这些脚本曾因上游删函数集体 ImportError (2026-11-09)
IMPORT_CHECK_MODULES = [
    "tools.batch._check_markup_entry_refactor",
    "tools.batch._eval_a2m_hits",
    "tools.batch._eval_accum_ma_gap",
    "tools.batch._eval_addline_only",
    "tools.batch._eval_channel_dist",
    "tools.batch._eval_fallback2",
    "tools.batch._eval_fb1_trend30",
    "tools.batch._eval_fb1_trend30_acc",
    "tools.batch._eval_fb2_timeliness",
    "tools.batch._eval_improvements",
    "tools.batch._eval_infer_stage",
    "tools.batch._eval_markup_accuracy",
    "tools.batch._eval_markup_share",
    "tools.batch._eval_stage_conflict",
    "tools.batch._eval_stage_dist",
    "tools.batch._eval_wta_influence",
    "tools.batch._eval_wta_vs_main",
    "tools.batch._verify_fb_channel_ast",
    "tools.batch._verify_strength",
]

# 命名规范 5 项 grep (handbook/naming-convention.md)
NAMING_PATTERNS = [
    (r"\bc[1-9]_|\b[1-9]a\b|\b[1-9]b\b|\bc_jump|\bc_leader|\brule_[1-9]", "工程缩写"),
    (r"\blag[s]?\b|\badd_lag|\bn_lag\b", "工程术语"),
    (r"[a-z]_[1-9]\b|\b[a-z]+_qoq_rising_prev[1-9]", "数字后缀"),
]


def _run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"⏱ 超时 {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def _py(*args: str) -> list[str]:
    return [sys.executable, *args]


# ── L0 静态 ────────────────────────────────────────────────────────

def l0_compile() -> tuple[bool, str]:
    rc, out = _run(_py("-m", "compileall", "-q", "tools", "core"), timeout=120)
    return rc == 0, out.strip().splitlines()[-1] if rc else ""


def l0_imports() -> tuple[bool, str]:
    """单进程批量 import —— 19 个模块一次搞定 (别用 19 次独立进程, 差 100 倍)"""
    code = (
        "import importlib,sys\n"
        f"mods={IMPORT_CHECK_MODULES!r}\n"
        "bad=[]\n"
        "for m in mods:\n"
        "    try: importlib.import_module(m)\n"
        "    except Exception as e: bad.append((m,repr(e)))\n"
        "print('FAIL' if bad else 'OK', len(mods)-len(bad), '/', len(mods))\n"
        "[print('  ',m,e) for m,e in bad]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    rc, out = _run(_py("-c", code), timeout=120)
    return rc == 0, out.strip()


# ── L1 单测 ────────────────────────────────────────────────────────

def l1_pytest() -> tuple[bool, str]:
    rc, out = _run(_py("-m", "pytest", "tests/", "-q"), timeout=600)
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    summary = next((ln for ln in reversed(lines) if "passed" in ln or "failed" in ln),
                   lines[-1] if lines else "")
    summary = summary.strip("= ").strip()
    return rc == 0, summary


# ── L2 守卫 ────────────────────────────────────────────────────────

def l2_guardrail() -> tuple[bool, str]:
    bad = []
    for kind in ("network", "factor", "parquet", "safe-fs"):
        rc, out = _run(_py("-m", "tools.batch.guardrail", "--check", kind), timeout=180)
        if rc != 0:
            bad.append(f"{kind}: rc={rc}")
    return (not bad), ("4 项 0 违规" if not bad else "; ".join(bad))


def _changed_files() -> list[str]:
    """本次工作区改动的 py 文件 (含已暂存)"""
    rc, out = _run(["git", "diff", "--name-only", "HEAD"], timeout=60)
    files = [ln.strip() for ln in out.splitlines() if ln.strip().endswith(".py")]
    # 未跟踪的新文件也要算 (新增文件 git diff 看不到)
    rc2, out2 = _run(["git", "ls-files", "--others", "--exclude-standard"], timeout=60)
    files += [ln.strip() for ln in out2.splitlines()
              if ln.strip().endswith(".py") and ln.strip() not in files]
    return files


def _added_lines(rel: str) -> set[int]:
    """该文件本次**新增/修改**的行号 (git diff -U0 解析 hunk 头)"""
    rc, out = _run(["git", "diff", "-U0", "HEAD", "--", rel], timeout=60)
    if rc != 0:
        return set()
    added: set[int] = set()
    for ln in out.splitlines():
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", ln)
        if not m:
            continue
        start = int(m.group(1))
        cnt = int(m.group(2) or 1)
        added.update(range(start, start + cnt))
    return added


def l2_naming() -> tuple[bool, str]:
    """命名规范 grep —— **只对本次新增/修改的行 fail**, 历史遗留只 warn

    (全量扫全仓的话历史遗留每次都红, 等于没有检查)
    """
    targets = _changed_files()
    if not targets:
        return True, "本次无改动文件 (跳过)"
    new_hits, old_hits = [], []
    for pat, label in NAMING_PATTERNS:
        rx = re.compile(pat)
        for rel in targets:
            p = ROOT / rel
            if not p.exists():
                continue
            added = _added_lines(rel)
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for i, ln in enumerate(lines, 1):
                if not rx.search(ln):
                    continue
                tag = f"{Path(rel).name}:{i} [{label}]"
                (new_hits if i in added else old_hits).append(tag)
    detail = "本次新增 0 命中"
    if old_hits:
        detail += f" (历史遗留 {len(old_hits)} 处未报)"
    if new_hits:
        detail = "新增违规: " + "; ".join(new_hits[:5])
    return (not new_hits), detail


# ── L3 全量扫描 ────────────────────────────────────────────────────

def parse_scan_md(md: str) -> dict:
    """从扫描汇总 md 里抠关键数字 (纯函数, 便于单测)"""
    def _grab(pat, default="?"):
        m = re.search(pat, md)
        return int(m.group(1)) if m else default
    return {
        "pool": _grab(r"原始候选.*?\|\s*(\d+)\s*→"),
        "hits": _grab(r"\*\*A→M 命中\*\*.*?\*\*(\d+)\*\*"),
        "standard": _grab(r"standard 档.*?\*\*(\d+)\*\*"),
        "loose": _grab(r"loose 档.*?\*\*(\d+)\*\*"),
        "codes": re.findall(r"\| `(\d{6})` \|", md),
    }


def l3_scan() -> tuple[bool, str]:
    """全市场扫描 1 次 (--no-full-report, 不出详报)"""
    out_md = "/tmp/verify_scan_out.md"
    rc, out = _run(_py(
        "-m", "tools.batch.wyckoff_a2m_scan",
        "--no-full-report", "--output", out_md,
    ), timeout=900)
    if rc != 0:
        tail = out.strip().splitlines()[-1:] or [""]
        return False, f"扫描失败 rc={rc}: {tail[0]}"
    try:
        md = Path(out_md).read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return False, f"读不到 md: {e}"
    d = parse_scan_md(md)
    return True, (f"候选池 {d['pool']} / 命中 {d['hits']}"
                  f" (standard {d['standard']} + loose {d['loose']})")


LAYERS = [
    ("L0", "py_compile 全仓", l0_compile),
    ("L0", f"批量 import {len(IMPORT_CHECK_MODULES)} 模块", l0_imports),
    ("L1", "pytest", l1_pytest),
    ("L2", "guardrail 4 项", l2_guardrail),
    ("L2", "命名规范 grep", l2_naming),
]


def main():
    ap = argparse.ArgumentParser(description="分层验证入口")
    ap.add_argument("--fast", action="store_true", help="L0+L1+L2 (默认)")
    ap.add_argument("--full", action="store_true", help="加上 L3 全市场扫描")
    args = ap.parse_args()

    todo = list(LAYERS)
    if args.full:
        todo.append(("L3", "全市场扫描 (--no-full-report)", l3_scan))

    print(f"\n{'='*62}\n  分层验证 ({'full' if args.full else 'fast'})"
          f"  共 {len(todo)} 项\n{'='*62}")
    t_all = time.time()
    failed = []
    for layer, name, fn in todo:
        t0 = time.time()
        ok, detail = fn()
        dt = time.time() - t0
        mark = "✅" if ok else "❌"
        print(f"  {mark} [{layer}] {name:32s} {dt:6.2f}s  {detail}")
        if not ok:
            failed.append(name)
    print(f"{'='*62}")
    print(f"  合计 {time.time()-t_all:.1f}s"
          + (f" | ❌ 失败: {', '.join(failed)}" if failed else " | 全部通过"))
    print(f"{'='*62}\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
