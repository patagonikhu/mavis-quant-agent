"""
tools/batch/a2m_report_common.py — A→M 详报生成的公共模块 (2026-11-05)

背景:
  `/t-wyckoff-a2m` (股票) 和 `/t-etf-wyckoff-a2m` (ETF) 都要做同一套详报流程:
    1. 扫 docs/<subdir>/ 里已有的详报 md, 认出哪些票是"历史留档"
    2. 逐只跑 /t-analyze 渲染 7 section 详报 (多进程)
    3. 给详报文件头打状态标记 (本次命中 / 曾命中现未命中), 幂等

  三步逻辑两边一样, 只有"主体用词"和"未命中原因"不同:
    股票 → "本票",    可能是 PE/质量/杠杆 过滤掉了
    ETF  → "本基金",  可能是 fund_type 过滤掉了

  所以抽到这里, 两边共用, 避免复制两份。
  (原实现内嵌在 tools/batch/wyckoff_a2m_scan.py, 2026-11-05 拆出)

用法:
    from tools.batch.a2m_report_common import (
        scan_existing_reports, stamp_report_status, report_worker,
    )

    existing = scan_existing_reports(Path("docs/wyckoff-a2m"))
    stamp_report_status(md_path, "hit", hit_note, marker="ETF_A2M_STATUS", subject="本基金")
"""
from __future__ import annotations

import time
from pathlib import Path


def scan_existing_reports(sub_dir: Path) -> dict:
    """扫 docs/<subdir>/ 已有的详报 md, 返回 {code: {"path": Path, "name": str}}

    文件名格式: analyze-{code6}-{中文名}.md
    code 从文件名第 2 段切 (兼容名字里有 '-' 的情况, 如 '双星-新材')

    2026-10-29 加: 支撑 --refresh 模式 (不清空, 刷新已有 + 补充新增)
    """
    out: dict = {}
    if not sub_dir.exists():
        return out
    for p in sorted(sub_dir.glob("analyze-*.md")):
        stem = p.stem                     # analyze-001219-青岛食品
        parts = stem.split("-", 2)        # ['analyze', '001219', '青岛食品']
        if len(parts) < 3:
            continue
        code = parts[1]
        if not (len(code) == 6 and code.isdigit()):
            continue
        out[code] = {"path": p, "name": parts[2]}
    return out


def stamp_report_status(
    md_path,
    status: str,
    hit_note: str = "",
    *,
    marker: str = "A2M_STATUS",
    subject: str = "本票",
    stale_note: str | None = None,
) -> bool:
    """在详报文件头插入本次 A→M 扫描状态标记

    Args:
        md_path:  详报 md 路径
        status:   'hit' (本次命中) / 'stale' (曾命中, 本次未命中)
        hit_note: 命中时附带的补充说明 (转变日/涨幅/阶段序列)
        marker:   HTML 注释标记名 (股票版 A2M_STATUS / ETF 版 ETF_A2M_STATUS)
        subject:  主体用词 ('本票' / '本基金')
        stale_note: 未命中原因文案, None = 用默认 (股票版口径)

    Returns: 是否成功写入

    做法: 在标题行 (第一行 '# ...') 后面插入一段引用块, 幂等 ——
    已有标记时先删掉旧的再插新的, 避免重复跑越积越多
    """
    if not md_path or not Path(md_path).exists():
        return False
    p = Path(md_path)
    try:
        lines = p.read_text(encoding="utf-8").split("\n")
    except Exception:
        return False

    # 1. 删掉旧的标记块 (幂等)
    #    连同标记块**前面紧挨的那个空行**一起删 —— 那是插入时自己加的,
    #    不删的话重复跑每次多一个空行 (2026-11-05 修: ETF 版对 merged 标记
    #    只改标记不重生成文件, 这个累积会很明显)
    cleaned = []
    skip_block = False
    for ln in lines:
        if ln.strip().startswith(f"<!-- {marker}"):
            skip_block = True
            if cleaned and cleaned[-1].strip() == "":
                cleaned.pop()
            continue
        if skip_block:
            if ln.strip() == f"<!-- /{marker} -->":
                skip_block = False
            continue
        cleaned.append(ln)

    # 2. 插新的
    if status == "hit":
        block = [
            f"<!-- {marker} hit -->",
            f"> 🟢 **{subject}为本次 A→M 扫描命中**{hit_note}",
            f"<!-- /{marker} -->",
        ]
    elif status == "merged":
        # 同一指数的其它代码 —— 本身也命中, 但被去重合并到代表基金
        block = [
            f"<!-- {marker} merged -->",
            f"> 🔗 **{subject}与代表基金跟踪同一指数, 已合并**{hit_note}",
            f"<!-- /{marker} -->",
        ]
    else:
        if stale_note is None:
            # 2026-11-12: 分档口径换市值, PE/质量/杠杆 三闸已整档作废 → 不能再提这些闸
            stale_note = ("可能威科夫阶段已离开 Markup, 或已跌出 A→M 命中集。"
                          "数据已刷新到最新交易日。")
        block = [
            f"<!-- {marker} stale -->",
            f"> ⚠️ **{subject}本次 A→M 扫描未命中** (曾命中过, 现留档) — {stale_note}",
            f"<!-- /{marker} -->",
        ]

    # 3. 插到标题行之后 (第一行是 '# ...')
    insert_at = 1 if cleaned and cleaned[0].startswith("#") else 0
    out = cleaned[:insert_at] + [""] + block + cleaned[insert_at:]
    try:
        p.write_text("\n".join(out), encoding="utf-8")
        return True
    except Exception:
        return False


def report_worker(task: tuple) -> tuple[str, bool, str, float]:
    """单只详报 worker (跑在子进程, mp.spawn 用 — 顶层函数才能 pickle)

    Args:
        task: (code, name, output_subdir) 三元组, 或
              (code, name, output_subdir, sector) 四元组 (2026-11-05 加 sector)

    Returns:
        (code, ok, md_path_or_err, elapsed)
    """
    code, name, output_subdir = task[0], task[1], task[2]
    sector = task[3] if len(task) > 3 else None
    t0 = time.time()
    try:
        from tools.batch.t_analyze_one import process_one as t_analyze_process_one
        r = t_analyze_process_one(code, name, output_subdir=output_subdir, sector=sector)
        elapsed = time.time() - t0
        ok = bool(r.get("ok"))
        path_or_err = r.get("md_path") if ok else r.get("err", "unknown")
        return (code, ok, path_or_err, elapsed)
    except Exception as e:
        return (code, False, f"{type(e).__name__}: {e}", time.time() - t0)
