"""
agent_data.py — 单只股票数据统一句柄 (2026-07-29 C 方案落地 v1.0)

设计目标: 合并 dump_data.py + data_source.py 拉数据逻辑, 用户只调 1 个类。

核心: max_age_min 自动判断要不要重拉
  - 0: 永远重拉 (baseline / 复盘)
  - 5: 5 分钟内免拉 (盘中盯盘)
  - 60: 1 小时内免拉 (日常分析, 默认)
  - 999999: 永不重拉 (看历史 dump)

用法:
  from tools.batch.agent_data import AgentData
  data = AgentData("002028")  # 默认 max_age_min=60
  print(data.get("chan_signals.wyckoff.stage"))  # 'Accumulation'
  data.render()  # 渲染 docs/analyze-002028-思源电气.md
  data.refresh()  # 强制重拉

向后兼容:
  tools/dump_data.py CLI 走 AgentData 类, 老用法 python -m tools.dump_data 002028 继续能用
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 项目根 (config/ 目录的父级)
# __file__ = tools/batch/agent_data.py → parent=tools/batch → parent.parent=tools → parent.parent.parent=project_root
PROJECT_ROOT = Path(__file__).parent.parent.parent
DUMP_DIR = PROJECT_ROOT / "data" / "dump"
DUMP_DIR.mkdir(parents=True, exist_ok=True)


class AgentData:
    """单只股票数据统一句柄 — 自动 ensure fresh"""

    def __init__(self, code: str, max_age_min: int = 60, force: bool = False):
        """
        Args:
            code: 6 位股票代码 (如 "002028")
            max_age_min: dump 超过几分钟就重拉, 默认 60
              - 0/force=True: 永远重拉
              - 5: 盘中盯盘
              - 60: 日常分析 (默认)
              - 999999: 永不重拉
            force: 强制重拉 (等价于 max_age_min=0)
        """
        self.code = code
        self.max_age_min = 0 if force else max_age_min
        self.dump_path = DUMP_DIR / f"{code}.json"
        self._data: Optional[dict] = None
        self._meta: dict = {
            "code": code,
            "max_age_min": max_age_min,
            "forced": force,
            "fresh": False,  # 本次是否重拉了
            "age_min": None,  # 读出来的 dump 几"分钟"前
        }
        self._ensure_fresh()

    # ============================================================
    # 1. 自动判断 fresh
    # ============================================================
    def _ensure_fresh(self):
        """核心: 自动判断要不要重拉"""
        if not self.dump_path.exists():
            self._fetch_and_dump()
            return

        if self.max_age_min <= 0:
            # 永远重拉
            self._fetch_and_dump()
            return

        existing = self._read_dump()
        age_min = self._age_minutes(existing)
        self._meta["age_min"] = age_min

        if age_min is not None and age_min <= self.max_age_min:
            # dump 新鲜, 直接用 — 但要校验关键字段存在
            missing = self._check_critical_fields(existing)
            if not missing:
                self._data = existing
                self._meta["fresh"] = False
                self._meta["age_min"] = age_min
                return
            # 关键字段缺失 → 走重拉, 不管 age
            self._meta["reason"] = f"missing fields: {missing}"
            self._fetch_and_dump()
            return

        # age_min 为 None (没 _meta) 或 太老 → 重拉
        self._fetch_and_dump()

    def _check_critical_fields(self, dump: dict) -> list:
        """校验 dump 是否含 v5.10+ 关键字段，缺失返回字段名列表

        v5.10.43+: dump 只存原始数据，factor 由 AnalysisEngine 实时计算
        关键字段: kline / fflow / eps_table（原始数据层）
        """
        critical = ["kline", "fflow", "eps_table"]
        return [k for k in critical if not dump.get(k)]

    def _age_minutes(self, dump: dict) -> Optional[float]:
        """读 dump 里的 _meta.pulled_at, 算到现在的分钟数

        额外检查: 若 kline 最新日期 < 今天(交易日), 强制返回 999999 触发重拉
        保证收盘后数据入库时, 下次 refresh 能拿到当日 K 线
        """
        pulled_at = (dump.get("_meta") or {}).get("pulled_at", 0)
        if not pulled_at:
            return None
        age_by_pull = (time.time() - pulled_at) / 60

        # kline 最新日期 < 今天 → 强制过期
        kl = dump.get("kline") or []
        if kl:
            last_date = str(kl[-1].get("trade_date", ""))  # "20260803"
            today = datetime.now().strftime("%Y%m%d")
            if last_date < today:
                return 999999  # 强制超出任何 max_age_min

        return age_by_pull

    def _read_dump(self) -> dict:
        with open(self.dump_path, encoding="utf-8") as f:
            return json.load(f)

    def _fetch_and_dump(self):
        """调 dump_data.dump_code 拉原始数据 + 写 dump（factor 由 AnalysisEngine 实时算）"""
        from tools.dump_data import dump_code, save_dump
        raw = dump_code(self.code)
        # 写 _meta 字段 (dump_code 不写, 我们补)
        raw["_meta"] = {
            "pulled_at": time.time(),
            "code": self.code,
            "tushare_calls": 0,  # TODO: 从 raw 提取
            "sina_calls": 0,
            "duration_ms": 0,
        }
        # 重写 dump (含 _meta)
        save_dump(raw, str(DUMP_DIR))
        self._data = raw
        self._meta["fresh"] = True

    # ============================================================
    # 2. 读字段
    # ============================================================
    def get(self, key: str, default: Any = None) -> Any:
        """读字段, 支持 dot path: get("chan_signals.wyckoff.stage")

        强制要求 dump 新鲜, 不存在时再读 disk
        """
        if self._data is None:
            self._data = self._read_dump()

        parts = key.split(".")
        cur = self._data
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return default
        return cur

    def raw(self) -> dict:
        """返回完整 dump 字典 (跟旧 dump_data.dump_code 返回一样)"""
        if self._data is None:
            self._data = self._read_dump()
        return self._data

    # ============================================================
    # 3. 操作
    # ============================================================
    def refresh(self) -> "AgentData":
        """强制重拉"""
        self._fetch_and_dump()
        return self

    def render(self, output_path: Optional[str] = None) -> str:
        """渲染报告到 docs/analyze-{code}-{name}.md

        Args:
            output_path: 自定义输出路径, 默认 docs/analyze-{code}-{name}.md

        Returns: 报告 markdown 字符串
        """
        from tools.analysis.analysis_data import AnalysisData
        from tools.render.report_renderer import render_report

        if self._data is None:
            self._data = self._read_dump()

        data_obj = AnalysisData.from_raw(self._data)
        sector = self._data.get("industry") or self._data.get("sector") or "—"
        md = render_report(data_obj, sector=sector)

        if output_path is None:
            name = self._data.get("name", "")
            output_path = PROJECT_ROOT / "docs" / f"analyze-{self.code}-{name}.md"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md)
        return md
        return md

    # ============================================================
    # 4. 元信息
    # ============================================================
    @property
    def meta(self) -> dict:
        """返回本次加载元信息 (是否重拉、age 等)"""
        return self._meta

    def __repr__(self):
        fresh_str = "FRESH" if self._meta.get("fresh") else "CACHED"
        age = self._meta.get("age_min")
        age_str = f", age={age:.1f}min" if age is not None else ""
        return f"<AgentData {self.code} {fresh_str}{age_str}>"


# ============================================================
# CLI 入口 (2026-07-29 C 方案)
#   python -m tools.agent_data 002028              # max_age_min=60
#   python -m tools.agent_data 002028 --force      # 永远重拉
#   python -m tools.agent_data 002028 --render     # 拉 + 渲染
#   python -m tools.agent_data 002028 --age 5      # 5 分钟内免拉
#   python -m tools.agent_data 002028 --analyze-only  # 永不重拉 (等同 --age 999999)
# ============================================================
def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="AgentData 统一数据入口 (C 方案 v1.0)")
    parser.add_argument("code", help="股票代码 (如 002028)")
    parser.add_argument("--age", type=int, default=60, help="max_age_min (默认 60)")
    parser.add_argument("--force", action="store_true", help="强制重拉 (等同 --age 0)")
    parser.add_argument("--render", action="store_true", help="渲染报告")
    parser.add_argument("--analyze-only", action="store_true", help="只读 dump (等同 --age 999999)")
    args = parser.parse_args()

    if args.analyze_only:
        max_age = 999999
    elif args.force:
        max_age = 0
    else:
        max_age = args.age

    print(f"📥 AgentData {args.code} (max_age_min={max_age})")
    data = AgentData(args.code, max_age_min=max_age)
    print(f"  - {data}")
    print(f"  - 价: ¥{data.get('current_price')}")
    print(f"  - K线: {len(data.get('kline') or [])} 根")
    chan = data.get("chan") or {}
    print(f"  - 周线段: {len((chan.get('weekly') or {}).get('segs', []))}")
    print(f"  - 日线段: {len((chan.get('daily') or {}).get('segs', []))}")
    print(f"  - 60分段: {len((chan.get('60min') or {}).get('segs', []))}")
    print(f"  - 日线中枢: {(chan.get('daily') or {}).get('hub', {}).get('valid', False)}")
    cs = data.get("chan_signals") or {}
    print(f"  - 威科夫 stage: {(cs.get('wyckoff') or {}).get('stage', 'N/A')}")
    print(f"  - 量价 verdict: {(cs.get('volume_price') or {}).get('verdict', 'N/A')}")
    fflow = data.get("fflow") or {}
    print(f"  - fflow verdict: {fflow.get('verdict', 'N/A')}")

    if args.render:
        print(f"\n🎨 渲染报告...")
        md = data.render()
        raw = data.raw()
        name = raw.get("name", "")
        report_path = PROJECT_ROOT / "docs" / f"analyze-{args.code}-{name}.md"
        print(f"✅ 报告已存: {report_path} ({len(md)} chars)")


if __name__ == "__main__":
    _cli()
