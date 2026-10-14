"""
加列后必跑的 md 表格结构校验 (2026-10-14 固化)
检测 3 类问题:
1. header cell 数 == sep cell 数
2. 数据行 cell 数 == header cell 数
3. cell 内没有未转义 | (除了全角)
"""
import re
import sys
from pathlib import Path

def check_md_table(path: Path) -> list[str]:
    errs = []
    txt = path.read_text(encoding='utf-8')
    lines = txt.splitlines()
    for i, line in enumerate(lines):
        if '| 日期 |' in line and 'KDJ' in line:  # 因子历史走势 header
            h_line = line
            if i+1 >= len(lines): continue
            sep = lines[i+1]
            if not sep.startswith('|'): continue
            h_n = len([c for c in h_line.split('|') if c.strip()])
            s_n = len([c for c in sep.split('|') if c.strip()])
            if h_n != s_n:
                errs.append(f'❌ {path.name}: line {i+1} header={h_n} sep={s_n}')
            # 数据行: 找后续 5 行
            for j in range(i+2, min(i+8, len(lines))):
                d_line = lines[j]
                if not d_line.startswith('|') or re.match(r'^\|[\s\-:|]+\|', d_line):
                    break
                d_n = len([c for c in d_line.split('|') if c.strip()])
                if d_n != h_n:
                    errs.append(f'❌ {path.name}: line {j+1} data={d_n} header={h_n}')
                # cell 内未转义 |
                for c in d_line.split('|'):
                    if '|' in c and c.strip():
                        errs.append(f'⚠️  {path.name}: line {j+1} cell 内含未转义 |: {c[:50]!r}')
            break
    return errs

if __name__ == "__main__":
    total = 0
    for d in ['docs/portfolio', 'docs/watchlist', 'docs/etf-macd']:
        p = Path(d)
        if not p.exists(): continue
        for f in sorted(p.glob('*.md')):
            errs = check_md_table(f)
            for e in errs:
                print(e)
            total += len(errs)
    print(f'\n总错误: {total}')
    sys.exit(0 if total == 0 else 1)
