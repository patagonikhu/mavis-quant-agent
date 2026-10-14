#!/usr/bin/env bash
# 加列后必跑: 检测 header/sep/data 三段 cell 数 + cell 内未转义 |
set -e
bash tools/with_venv.sh python tools/check_md_table.py
