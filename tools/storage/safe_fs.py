"""
tools/storage/safe_fs.py — 安全文件操作 (2026-11-02)

**为什么有这个文件**: 批量脚本曾用 `p.unlink()` 直接删用户数据 (docs/ 下的历史详报),
而 `docs/` 被 .gitignore 排除 → 删了不可恢复. 另一个脚本直接覆盖写 `config/watchlist.json`
(持仓配置), 中途异常会写出半截 JSON.

本模块把「不许毁用户数据」这条规则**下沉到代码**, 不靠 SKILL.md 文字提醒:

  safe_delete()      一律移到 ~/.Trash/, 永不 unlink (可恢复)
  atomic_write_json() 写前备份 + 原子写 (tmp + replace), 不留半截文件
  PROTECTED_PATHS    受保护路径, safe_delete 直接拒绝

用法:
    from tools.storage.safe_fs import safe_delete, atomic_write_json

    safe_delete(p)                    # → ~/.Trash/mavis-safe-fs-<ts>/<name>
    atomic_write_json(path, obj)      # → 备份到 <path>.bak-<ts> + 原子替换
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 受保护路径 (safe_delete 直接拒绝, 不给 --force 也拒)
# ─────────────────────────────────────────────────────────────
# 这些是用户数据 / 核心配置, 任何批量脚本都不该删
PROTECTED_PATHS = {
    "config/watchlist.json",
    "config/project.yaml",
    "config/sources.yaml",
    "data/analysis_cache.db",
}

# 报告根目录: 删单个文件可以 (safe_delete 走 Trash), 但删整个目录要显式
REPORT_ROOT = Path("docs")


def _is_protected(path: Path) -> bool:
    """检查是否在受保护名单里 (按项目相对路径匹配)"""
    try:
        rel = path.resolve().relative_to(Path.cwd().resolve())
    except (ValueError, OSError):
        rel = path
    rel_str = str(rel)
    return any(rel_str == p or rel_str.endswith("/" + p) for p in PROTECTED_PATHS)


def _trash_dir() -> Path:
    """回收站目录 (带时间戳, 避免多次清理互相覆盖)"""
    return Path.home() / ".Trash" / f"mavis-safe-fs-{int(time.time())}"


def safe_delete(path: Path, reason: str = "") -> bool:
    """安全删除单个文件/目录 — 移到 ~/.Trash/, **永不 unlink**。

    Args:
        path:   要删的路径 (文件或目录)
        reason: 备注 (写进 trash 目录的 README, 方便事后追溯)

    Returns:
        True = 已移到回收站 / False = 没动 (受保护 / 出错)

    用法:
        for f in old_files:
            safe_delete(f, reason="A→M 目录清理")
    """
    p = Path(path)
    if not p.exists():
        return False

    # 受保护 → 拒绝
    if _is_protected(p):
        print(f"  🛑 safe_delete 拒绝: {p} 在 PROTECTED_PATHS 里, 不删")
        return False

    # 整个 docs/ 目录不删 (只删里面的单个文件)
    try:
        if p.resolve() == (Path.cwd() / REPORT_ROOT).resolve():
            print(f"  🛑 safe_delete 拒绝: {p} 是报告根目录, 不删")
            return False
    except OSError:
        pass

    trash = _trash_dir()
    try:
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / p.name
        # 同名冲突 → 加序号
        i = 1
        while dest.exists():
            dest = trash / f"{p.stem}-{i}{p.suffix}"
            i += 1
        shutil.move(str(p), str(dest))
        if reason:
            (trash / "_README.txt").write_text(
                f"mavis safe_delete @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"原因: {reason}\n\n"
                + "\n".join(sorted(x.name for x in trash.iterdir() if x.name != "_README.txt")),
                encoding="utf-8",
            )
        return True
    except Exception as e:
        # 移到回收站失败 → **保留原文件** (绝不退回 unlink)
        print(f"  ⚠️ safe_delete 失败 ({p}): {e} — 原文件保留, 未删除")
        return False


def atomic_write_json(path: Path, data, backup: bool = True, indent: int = 2) -> bool:
    """原子写 JSON (写前备份 + tmp + replace)。

    跟 DataStore.save_watchlist 的区别: **多一个写前备份**。
    用户的持仓配置丢一次就是灾难, 宁可多留个 .bak-<ts> 文件。

    Args:
        path:    目标文件
        data:    可 JSON 序列化的对象
        backup:  True = 写前把旧文件备份成 <path>.bak-<ts>
        indent:  JSON 缩进

    Returns:
        True 成功 / False 失败 (原文件保持不变)
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"  ⚠️ atomic_write_json 失败 (建目录 {p.parent}): {e}")
        return False

    # 写前备份
    if backup and p.exists():
        try:
            bak = p.with_name(f"{p.name}.bak-{int(time.time())}")
            shutil.copy2(p, bak)
        except Exception as e:
            print(f"  ⚠️ 备份失败 ({p} → .bak): {e} — 继续写")

    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:
        print(f"  ⚠️ atomic_write_json 失败 ({p}): {e} — 原文件未改动")
        # 清掉半截 tmp
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


if __name__ == "__main__":
    # 自检: 演示 + 验证保护机制
    print("=" * 60)
    print("safe_fs 自检")
    print("=" * 60)

    print(f"\nPROTECTED_PATHS ({len(PROTECTED_PATHS)} 个):")
    for x in sorted(PROTECTED_PATHS):
        print(f"  - {x}")

    print("\n测试 1: safe_delete 拒绝受保护路径")
    p = Path("config/watchlist.json")
    if p.exists():
        ok = safe_delete(p, reason="自检")
        print(f"  → {'❌ 竟然删了' if ok else '✅ 正确拒绝'} (文件还在: {p.exists()})")
    else:
        print("  (文件不存在, 跳过)")

    print("\n测试 2: safe_delete 移到回收站")
    tmp = Path("/tmp/_safe_fs_test.md")
    tmp.write_text("test", encoding="utf-8")
    ok = safe_delete(tmp, reason="自检")
    print(f"  → {'✅ 已移到回收站' if ok else '❌ 失败'} (原文件还在: {tmp.exists()})")

    print("\n测试 3: atomic_write_json 备份")
    tmp2 = Path("/tmp/_safe_fs_test.json")
    tmp2.write_text('{"a": 1}', encoding="utf-8")
    ok = atomic_write_json(tmp2, {"a": 2})
    print(f"  → {'✅ 成功' if ok else '❌ 失败'}, 内容 = {tmp2.read_text()}")
    for b in tmp2.parent.glob("_safe_fs_test.json.bak-*"):
        print(f"  备份: {b.name} = {b.read_text()}")
        b.unlink()
    tmp2.unlink(missing_ok=True)
