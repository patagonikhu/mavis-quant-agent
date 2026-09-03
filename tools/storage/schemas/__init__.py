"""
tools/storage/schemas/ — 手工维护配置 (sectors / events / watchlist)

2026-09-03 v6.1.1 新建: 准备迁移 watchlist/sectors/events 散落读,
但目前是占位 — 读路径分散在 batch/research/factors 等模块,
完整迁移是后续工程.

设计:
  - watchlist.py:  read + add_code / remove_code / save 全原子操作
  - sectors.py:     read + add_stock_to_sector / list_codes
  - events.py:      read + append_event
"""
