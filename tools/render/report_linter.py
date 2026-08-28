"""
report_linter.py — 报告完整性校验 (v1.0, 2026-07-21)

作用:
  1. 读已生成的 md 文件, 检查 22 section 是否齐全
  2. 识别关键数据 (价格/EPS/fflow 等) 是否缺失
  3. 输出校验报告, 追加到 md 末尾
  4. 返回 dict 给调用方, 可作为 quality gate

使用方式:
  from tools.render.report_linter import lint_report
  result = lint_report("docs/analyze-002371-北方华创.md")
  if result["completeness_pct"] < 50:
      print("⚠️ 报告不完整, 需重跑")
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path (直接 python3 tools/render/report_linter.py 时 PYTHONPATH 可能未设)
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# === file mode 路径修正 (2026-07-23 修复) ===
# 之前 python3 tools/render/report_linter.py ... 跑时, sys.path[0] = tools/ 目录,
# 导致 `from tools.render.report_schema import` 失败, REQUIRED_SECTIONS 退化成 fallback 18 列表
# (16/18 = 88% 假象, 报告实际是 31/32 = 100%)
# 这里自动把项目根目录加到 sys.path, 修复 import
_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TOOLS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 必填 section 清单 (2026-07-22 改用 schema 真源, 格式稳定)
# ============================================================

# 从 schema 读 (单一真源: tools/render/report_schema.py)
# 不设 fallback — import 失败应该直接报错, 而不是静默走旧数据
from tools.render.report_schema import get_section_titles, validate_schema
_schema_check = validate_schema()
if not _schema_check["valid"]:
    print(f"⚠️ Schema 校验失败: {_schema_check['errors']}")
REQUIRED_SECTIONS = get_section_titles()



# ============================================================
# 关键数据点 (用于检查数据是否真有)
# ============================================================

KEY_DATA_PATTERNS = {
    "价格": r"股价:\s*¥[\d.]+|当前价\s*[\|格]?\s*¥[\d.]+|¥[\d.]+\s*\|",  # 2026-07-24 改: 容忍"当前价格"和"价格"列
    "EPS 表格": r"\|\s*20\d{2}[AE]\s*\|\s*[\d.]+\s*\|",
    "MA 表格": r"\|\s*MA(5|20|60|120)\s*\|",
    "fflow 明细": r"\d{4}-\d{2}-\d{2}\s*\|\s*[+-]?[\d.]+",
    "PEG 数值": r"PEG_真实.*(\d+|—)",  # 2026-07-24 改: 容忍 999 或 "—" (PE 缺失)
    "DCF L 数值": r"r=8%.*[\d.]+\s*亿|L\s*/\s*可达利润",
    "L/可达利润": r"L\s*/\s*可达利润\s*\|?\s*[\d.]+",
    "板块 MA20 偏离": r"MA20\s*偏离.*[\+\-]?[\d.]+%",
    "止盈价": r"\+20%\s*\|\s*¥[\d.]+|当前\s*→\s*\+20%.*¥[\d.]+",
    "止损价": r"-10%.*¥[\d.]+",
    "评级": r"(🥇|🥈|🥉|⚠️|❌).*[重仓|标准|轻仓|观察|不买]",
    # ===== 5 方法 × 3 周期 矩阵 必填 (匹配 schema 真源: tools/render/report_schema.py, id=method_matrix) =====
    "因子矩阵标题": r"##\s*🎯\s*5\s*方法\s*×\s*3\s*周期",
    # render 真实输出: `**场景**: C (震荡观望) | **共振数**: 5 重 | **行动**: ⬜ 震荡观望`
    "5方法场景": r"\*\*场景\*\*[：:]\s*[ABCDEabcde]\s*[\(\uff08]",
    "5方法共振数": r"\*\*共振数\*\*[：:]\s*\d+\s*重",
    "5方法行动": r"\*\*行动\*\*[：:]\s*(🥇|🥈|🥉|🟢|🟡|⬜|❌)",
    "5方法总分": r"(5\s*方法\s*总分|总分).{0,30}[\-\d.]+",  # "5方法总分 < 1.5" 或 "总分 -0.1"
}


# ============================================================
# 主校验函数
# ============================================================

def lint_report(md_path: str) -> dict[str, Any]:
    """
    校验报告完整性

    Returns:
        {
            "file": str,
            "total_sections": int,
            "present_sections": int,
            "missing_sections": list[str],
            "completeness_pct": int,
            "key_data_found": dict[str, bool],
            "warnings": list[str],
        }
    """
    path = Path(md_path)
    if not path.exists():
        return {
            "file": md_path,
            "error": "FILE_NOT_FOUND",
        }

    content = path.read_text(encoding="utf-8")

    # === 升级: 排除 linter 自身报告段, 防止"自引用"误报 ===
    # 找到 "## 🔍 Linter 校验报告" 段, 只扫描它之前的内容
    linter_marker = "## 🔍 Linter 校验报告"
    if linter_marker in content:
        content = content.split(linter_marker)[0]

    # 2026-07-25: 优先用 section id 注释匹配, fallback 才用标题文案
    # report_renderer 会在每个 section 标题前注入 `<!-- id:xxx -->` 注释
    # 这样 Linter 不依赖标题文案, 改标题不影响 Linter
    # 注: 用 list 保序 (set 是无序的, 会让 _find_idx 顺序错乱)
    md_ids = re.findall(r"<!--\s*id:\s*([a-z0-9_]+)\s*-->", content)

    # 1. 检查 section 齐全 (linter 自身段不查, 避免自引用)
    sections_to_check = [s for s in REQUIRED_SECTIONS if "Linter 校验报告" not in s]
    # 优先用 id 注释匹配 (从 REPORT_SECTIONS 取 id), fallback 标题文案
    try:
        from tools.render.report_schema import get_section_ids
        id_to_title = {}
        from tools.render.report_schema import REPORT_SECTIONS
        for sec in REPORT_SECTIONS:
            id_to_title[sec["id"]] = sec["title"]
    except ImportError:
        id_to_title = {}

    def _section_present(title: str) -> bool:
        """检查 section 是否存在: 优先用 id 注释, fallback 标题"""
        # 标题 → id 反查
        sid = id_to_title.get(title)
        if sid and sid in md_ids:
            return True
        # fallback 标题文案
        return f"## {title}" in content or f"##  {title}" in content

    missing = [s for s in sections_to_check if not _section_present(s)]
    present = len(sections_to_check) - len(missing)
    completeness = int(present / max(len(sections_to_check), 1) * 100)

    # 2. 检查关键数据
    key_data = {}
    for name, pattern in KEY_DATA_PATTERNS.items():
        key_data[name] = bool(re.search(pattern, content))

    # 3. 警告 (升级: 加占位符检测 + 段列表/中枢列表检测, 防止"数据丢"漏检)
    warnings = []
    if not key_data["价格"]:
        warnings.append("⚠️ 未找到价格 (实时价可能没拉到)")
    if not key_data["EPS 表格"]:
        warnings.append("⚠️ 未找到 EPS 表格 (datacenter 可能没拉到)")
    if not key_data["MA 表格"]:
        warnings.append("⚠️ 未找到 MA 表 (K线可能不够)")
    if not key_data["PEG 数值"] and "PEG" in content:
        warnings.append("⚠️ PEG section 存在但无数值 (LLM 可能没算)")
    if not key_data["DCF L 数值"] and "DCF" in content:
        warnings.append("⚠️ DCF L section 存在但无数值")
    if not key_data["fflow 明细"] and "fflow" in content:
        warnings.append("⚠️ fflow section 存在但无明细")

    # 2026-07-24: 逻辑校验 (升级, 之前只查"存在"不查"合理")
    # 检查"价格 vs 中枢"距离, 防止"位置: 下方⚠️" 但实际是"跌穿" 的语义模糊
    m_hub = re.search(r"中枢区间:\*\*\s*¥([\d.]+)\s*~\s*¥([\d.]+)", content)
    m_price = re.search(r"当前价格:\*\*\s*¥([\d.]+)", content)
    # 精确化: 抓 "(位置: XXX)" 里的 XXX, ** 可选
    m_pos = re.search(r"\(位置:\s*(?:\*\*)?\s*(.+?)(?:\s|\))", content)
    if m_hub and m_price and m_pos:
        try:
            p = float(m_price.group(1))
            hl = float(m_hub.group(1))
            hh = float(m_hub.group(2))
            stated_pos = m_pos.group(1).strip()
            # 计算距中枢下沿的偏离度
            dev_low = (p - hl) / hl * 100  # 负数=下方, 正数=上方
            # 校验位置表述和真实距离是否一致
            if p < hl * 0.95 and "跌穿" not in stated_pos:
                # 价格跌穿中枢下沿 5%+, 但报告没标"跌穿"
                warnings.append(
                    f"🔴 中枢位置表述模糊: 价格 ¥{p:.2f} 跌穿下沿 ¥{hl:.2f} ({dev_low:.1f}%), "
                    f"但报告写 '{stated_pos}' (应是'跌穿🔴')"
                )
            elif hl * 0.95 <= p < hl and "跌穿" in stated_pos:
                # 价格只破 <5%, 但报告标"跌穿" (过严)
                warnings.append(
                    f"🔴 位置术语过严: 实际距中枢下沿 {dev_low:.1f}% (未到 5% 阈值), 报告写'跌穿'过严, 应是'下方⚠️'"
                )
            elif hl <= p <= hh and ("下方" in stated_pos or "上方" in stated_pos):
                warnings.append(
                    f"🔴 位置术语不一致: 价格在中枢内, 但报告写'{stated_pos}' (应是'内部⬜')"
                )
        except Exception:
            pass
    # ===== 2026-07-24 新增: 5 方法矩阵 必填校验 (硬保证稳定显示) =====
    if not key_data["因子矩阵标题"]:
        warnings.append("🔴 缺 '5 方法 × 3 周期 矩阵' section 标题 (硬保证失败)")
    if not key_data["5方法场景"]:
        warnings.append("🔴 缺 '场景' (A-E) (5 方法矩阵必填)")
    if not key_data["5方法共振数"]:
        warnings.append("🔴 缺 '共振数' (数字 + '重') (5 方法矩阵必填)")
    if not key_data["5方法行动"]:
        warnings.append("🔴 缺 '行动' (🥇/🥈/🥉/❌) (5 方法矩阵必填)")
    if not key_data["5方法总分"]:
        warnings.append("🔴 缺 '总分' 字段 (跨周期公式必填)")

    # === 升级: 占位符检测 (2026-07-22 加, 防止"数据丢"漏检) ===
    # 2026-07-23 加: 3 个特定占位符 (sync_stock 之前埋的坑, 实算后必须消失)
    placeholder_patterns = [
        ("未填", "数据未填实"),
        ("未生成", "占位符未生成"),
        ("未计算", "占位符未计算"),
        ("未完成", "占位符未完成"),
        ("⚠️ 数据状态", "显式占位符"),
        # === 2026-07-23: 缠论补充 3 个实算前的占位符 (commit 59d660c 已修) ===
        ("见 fflow 段, Tushare.money_flow 真数据", "量价_OBV 占位符 (应实算)"),
        ("见大盘背景段", "多市场共振 占位符 (应实算)"),
        ("N/A (Sina 60分 K线 3795 根硬上限)", "SMC-OB 占位符 (应实算)"),
        # === 2026-08-15: 实算失败的硬性残行 (factor_history bug 期间 render 留下的脏数据) ===
        # 背景: analysis_result_signals.py 字典字面量塞赋值时, render 阶段不依赖此模块也能成功,
        # 留下 "❌ 历史计算失败: ..." 残行在 57 份 md 报告里, 直到下一次 render 才覆盖
        # 防护: 任何 md 里出现"历史计算失败"→ 立即 FAIL, 强制重跑 refresh_all
        ("历史计算失败", "factor_history 实算失败残行 (必须重跑 refresh_all.sh)"),
    ]
    for pattern, desc in placeholder_patterns:
        # 排除合法的"未触发"等业务术语
        matches = [line for line in content.split("\n") if pattern in line]
        if matches:
            for line in matches[:3]:  # 只报前 3 个
                warnings.append(f"⚠️ 占位符 [{desc}]: {line.strip()[:80]}")

    # === 升级: 段列表检测 (缠论完整数据 段必须有 seg_idx + 子段) ===
    if "## 📊 多周期信号汇总" in content:
        has_seg_idx = "seg_idx" in content
        has_seg_table = "起价" in content and "止价" in content and "笔数" in content
        if not has_seg_idx:
            warnings.append("⚠️ 多周期信号汇总缺 `seg_idx` 字段")
        if not has_seg_table:
            warnings.append("⚠️ 多周期信号汇总缺段列表")

    # === 升级: 中枢列表检测 (必须展开中枢构成 seg_idx) ===
    if "中枢" in content:
        # 检查是否有"中枢构成"段 (新格式特征)
        if "中枢构成" not in content and "中枢区间" not in content:
            warnings.append("⚠️ 缠论中枢只有总览, 缺详细子段列表")

    # === 2026-07-25 新增: markdown 格式检测 (抓"标题紧接表格"bug) ===
    # 抓"粗体标题行末直接换行表格行"的情况 (中间无空行)
    # 关键: ** 必须跟 \n| 之间无空行, 即同一段
    # python-markdown 库把这种格式当普通段落, 表格被吞进 <p> 标签
    md_format_bugs = re.findall(
        r"^\*\*[^*\n【]*?\*\*\n\|[^\n]+\|", content, re.MULTILINE
    )
    if md_format_bugs:
        # 抽 3 个示例
        examples = md_format_bugs[:3]
        for ex in examples:
            # 提取标题
            title_m = re.match(r"^\*\*([^*\n]+?)\*\*", ex)
            title = title_m.group(1).strip() if title_m else "?"
            warnings.append(
                f"🔴 markdown 格式错: `**{title}**` 后缺空行就接表格, "
                f"表格会被 python-markdown 吞掉 (改成 `**{title}**\\n\\n| ...`)"
            )

    # === 2026-07-25 新增: 5 方法分析 section 必须有表格 ===
    # 5 方法分析的 3 个子 section (周线/日线/60分) 各应有缠论详情表 + 走段表
    m_5method = re.search(r"## 🔍 5 方法分析(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if m_5method:
        section_5m = m_5method.group(1)
        # 数子 section
        sub_count = len(re.findall(r"### 📋 (周线|日线|60分)分析", section_5m))
        # 数表格行 (| ... | 模式, 连续 2 行以上)
        tables = re.findall(r"(?:^\|[^\n]*\|\n){2,}", section_5m, re.MULTILINE)
        if sub_count > 0:
            expected = sub_count * 2  # 至少缠论详情 + 最近走段
            if len(tables) < expected:
                warnings.append(
                    f"🔴 5 方法分析 {sub_count} 子 section, "
                    f"但只有 {len(tables)} 个表格 (期望 ≥ {expected} 个: "
                    f"每子 section 至少缠论详情 + 最近走段)"
                )

    # === 升级: fflow 数据源标注检测 ===
    if "fflow" in content.lower() or "主力" in content:
        has_source_label = "Tushare.money_flow" in content or "Tushare 真实" in content or "OBV 派生" in content
        if not has_source_label:
            warnings.append("⚠️ fflow 段无数据源标注 (Tushare.money_flow / OBV 派生)")

    # === 新增: fflow 数值合理性检测 (单位 bug 检测) ===
    # 正常单股 fflow 5 日净额 ≤ ±100 亿, 超过 500 亿必是单位错误
    m_fflow_total = re.search(r"5日主力净额[：:]\s*([+-]?[\d,.]+)\s*亿", content)
    if m_fflow_total:
        try:
            val = abs(float(m_fflow_total.group(1).replace(",", "")))
            if val > 500:
                warnings.append(
                    f"🔴 fflow 数值异常: {m_fflow_total.group(1)} 亿 (超过 500 亿, 疑似单位 bug — 应是 {val/1e4:.2f} 亿)"
                )
        except ValueError:
            pass

    # === 新增: section 顺序检测 (基于 report_schema.REPORT_SECTIONS) ===
    try:
        from tools.render.report_schema import get_ordered_titles
        ordered_titles = get_ordered_titles()
        # 只检查 CLAUDE.md 红字铁律: 缠论 1️⃣2️⃣ 必须在 PEG 5️⃣ 之前
        # 2026-07-25: 优先用 id 注释 (按 REPORT_SECTIONS 顺序), fallback 标题文案
        section_titles_in_doc = re.findall(r"^##\s+(.+)$", content, re.MULTILINE)
        # 构建 id → 标题的索引顺序
        section_id_to_title = {}
        try:
            from tools.render.report_schema import REPORT_SECTIONS
            for i, sec in enumerate(REPORT_SECTIONS):
                section_id_to_title[sec["id"]] = sec["title"]
        except ImportError:
            pass

        def _find_idx(keyword: str) -> int:
            # 优先: 找 id 注释
            try:
                target_id = next(sid for sid, t in section_id_to_title.items() if keyword in t)
                for i, doc_id in enumerate(md_ids):
                    if doc_id == target_id:
                        return i
            except StopIteration:
                pass
            # fallback 标题文案
            for i, t in enumerate(section_titles_in_doc):
                if keyword in t:
                    return i
            return -1

        # 2026-07-25: 用 id 注释精确找位置 (不用关键词模糊匹配)
        # 2️⃣ 5 方法详情 = chan_supplement (chan_signals 已于 7-29 废弃合并入 factor_history)
        # 5️⃣ PEG = peg
        def _find_id_idx(target_id: str) -> int:
            for i, doc_id in enumerate(md_ids):
                if doc_id == target_id:
                    return i
            return -1

        supp_idx = _find_id_idx("chan_supplement")
        matrix_idx = _find_id_idx("method_matrix")
        peg_idx = _find_id_idx("peg")
        dcf_idx = _find_id_idx("dcf")

        if supp_idx >= 0 and peg_idx >= 0 and peg_idx < supp_idx:
            warnings.append(
                f"🔴 顺序违反铁律: PEG (第{peg_idx+1}个) 在 5方法详情 (第{supp_idx+1}个) 之前 — CLAUDE.md 5️⃣ 必须在 2️⃣ 之后"
            )
        # 2026-08-26: 删 "5 方法详情 section 缺失" 硬保证 (schema 已删 chan_supplement, 详情段非必填)
        if matrix_idx < 0:
            warnings.append("🔴 5 方法 × 3 周期 矩阵 section 缺失 (id:method_matrix)")
    except ImportError:
        pass

    # === 新增: 连续空行检测 ===
    # 3+ 连续空行 = 格式垃圾 (历史 bug, 2026-07-25 收敛后不再产生)
    blank_run_matches = list(re.finditer(r"\n{4,}", content))  # \n{4,} = 3+ 空行
    if blank_run_matches:
        worst = max(len(m.group()) - 1 for m in blank_run_matches)
        warnings.append(
            f"🔴 发现 {len(blank_run_matches)} 处连续空行 (最多 {worst} 个连续空行) — 渲染时空行未清理"
        )

    # === 重复 section 检测 (之前在 return 后, 修复死代码) ===
    section_titles_all = re.findall(r"^##\s+(.+)$", content, re.MULTILINE)
    title_counts: dict[str, int] = {}
    for t in section_titles_all:
        t_base = re.sub(r"\s*[-—(].*$", "", t).strip()  # 去后缀
        title_counts[t_base] = title_counts.get(t_base, 0) + 1
    for t, c in title_counts.items():
        if c > 1:
            warnings.append(f"🔴 section 重复 {c} 次: '{t}' (渲染时多次插入)")

    # === 空数据表格检测 (之前在 return 后, 修复死代码) ===
    sections_split = re.split(r"^## ", content, flags=re.MULTILINE)
    for sec in sections_split[1:]:
        sec_title = sec.split("\n")[0].strip()
        for table_match in re.finditer(r"((?:^|\n)\|[^\n]+\|(?:\n\|[-: |]+\|)?(?:\n\|[^\n]+\|)+)", sec):
            table = table_match.group()
            rows = [r for r in table.strip("\n").split("\n") if r.startswith("|")]
            if len(rows) < 3:
                continue
            data_rows = rows[2:]
            if not data_rows:
                continue
            empty_row_count = sum(
                1 for r in data_rows
                if all(c.strip() in ("", "—", "-", "N/A") for c in r.strip("|").split("|"))
            )
            if empty_row_count / len(data_rows) >= 0.6:
                warnings.append(
                    f"🔴 '{sec_title[:30]}' 表格 60%+ 行全'—': {empty_row_count}/{len(data_rows)} (模板没接数据)"
                )

            # === 2026-08-01: markdown 表格 cell 错位检测 ===
            # bug 来源: cell 内部用 ' | ' join 多 label, | 是 cell 分隔符导致错位
            # 排除 \| 转义符, 它在 markdown 表格里不是分隔符
            _ESC_PIPE = "\\|"  # raw 写法, 避免 SyntaxWarning
            header_n = rows[0].replace(_ESC_PIPE, "").count("|") - 1
            sep_n = rows[1].replace(_ESC_PIPE, "").count("|") - 1
            if header_n != sep_n:
                warnings.append(
                    f"🔴 '{sec_title[:30]}' 表格表头 vs 分隔符列数不匹配: {header_n} vs {sep_n}"
                )
            for r in data_rows:
                # 2026-08-01: 排除 \| 转义符, 它在 markdown 表格里不是分隔符
                n = r.replace(_ESC_PIPE, "").count("|") - 1
                if n != header_n:
                    # 截断 cell 内容避免 warning 过长
                    first_cells = r.strip("|").split("|")[:3]
                    preview = " | ".join(c.strip()[:20] for c in first_cells)
                    warnings.append(
                        f"🔴 '{sec_title[:30]}' 表格行 cell 数 {n} ≠ 表头 {header_n}: {preview}... "
                        f"(可能 cell 内部含未转义 '|', 检查 render 代码)"
                    )
                    break  # 每个表格只报一行, 避免刷屏

    # === bullet 压成单行检测 ===
    suspicious_inline = re.findall(
        r"^-\s+\*\*[^*]+:\*\*\s+[^\n-]+-\s+\*\*[^*]+:\*\*",
        content, flags=re.MULTILINE
    )
    if suspicious_inline:
        warnings.append(
            f"🔴 发现 {len(suspicious_inline)} 处 bullet 压成单行 (退场信号/监控触发点典型 bug)"
        )

    return {
        "file": md_path,
        "total_sections": len(REQUIRED_SECTIONS),
        "present_sections": present,
        "missing_sections": missing,
        "completeness_pct": completeness,
        "key_data_found": key_data,
        "warnings": warnings,
        "placeholder_count": sum(1 for p, _ in placeholder_patterns if p in content),
        "seg_idx_found": has_seg_idx if "## 📐 多周期走势数据" in content else None,
    }


def append_lint_to_md(md_path: str, result: dict | None = None) -> dict:
    """校验报告 + 追加到 md 末尾 (智能: 检测已有则替换, 没有则追加)"""
    if result is None:
        result = lint_report(md_path)

    path = Path(md_path)
    if not path.exists():
        return result

    # 生成 lint 块
    block = f"\n\n---\n\n## 🔍 Linter 校验报告 (自动追加, {result.get('check_time', '')})\n\n"
    block += f"- **完整度:** {result['completeness_pct']}% ({result['present_sections']}/{result['total_sections']} section)\n"

    if result.get("missing_sections"):
        block += f"- **缺失 section:** {', '.join(result['missing_sections'])}\n"

    # 关键数据
    block += "\n**关键数据点:**\n"
    for k, v in result.get("key_data_found", {}).items():
        block += f"- {k}: {'✅' if v else '❌'}\n"

    if result.get("warnings"):
        block += f"\n**⚠️ 警告:**\n"
        for w in result["warnings"]:
            block += f"- {w}\n"

    block += f"\n> 本校验由 `tools/render/report_linter.py` 自动生成\n"

    # 智能去重: 检测已有 Linter 块, 替换而不是追加
    content = path.read_text(encoding="utf-8")
    # 匹配: "## 🔍 Linter 校验报告" 到文件末尾的所有内容
    pattern = r"\n*---\n*\n## 🔍 Linter 校验报告.*$"
    if re.search(pattern, content, re.DOTALL):
        # 已有, 替换
        new_content = re.sub(pattern, block, content, flags=re.DOTALL)
        action = "replaced"
    else:
        # 没有, 追加
        new_content = content + block
        action = "appended"

    path.write_text(new_content, encoding="utf-8")
    result["_action"] = action
    return result


# ============================================================
# 批量校验 (扫所有 analyze-*.md)
# ============================================================

def lint_all_reports(docs_dir: str = "docs") -> list[dict]:
    """批量校验 docs/ 下所有 analyze-*.md"""
    docs_path = Path(docs_dir)
    if not docs_path.exists():
        return []

    results = []
    for md_file in sorted(docs_path.glob("analyze-*.md")):
        result = lint_report(str(md_file))
        results.append(result)

    return results


def lint_summary(results: list[dict]) -> str:
    """批量校验结果汇总表"""
    if not results:
        return "无报告"

    lines = ["| 文件 | 完整度 | 缺失 | 警告数 |", "|---|---|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['file']} | ❌ | ERROR | — |")
            continue
        lines.append(
            f"| {r['file']} | {r['completeness_pct']}% "
            f"({r['present_sections']}/{r['total_sections']}) "
            f"| {len(r.get('missing_sections', []))} 个 "
            f"| {len(r.get('warnings', []))} 个 |"
        )
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        target = sys.argv[1]
        if target == "all":
            results = lint_all_reports()
            print(lint_summary(results))
        else:
            result = lint_report(target)
            result = append_lint_to_md(target, result)
            print(f"完整度: {result['completeness_pct']}%")
            print(f"缺失: {result.get('missing_sections', [])}")
            print(f"警告: {result.get('warnings', [])}")
    else:
        print("Usage: python3 tools/render/report_linter.py <file.md>")
        print("       python3 tools/render/report_linter.py all")
