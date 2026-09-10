# 网络调用守门员报告

> 扫描: `tools/`  |  Skill: `all`  |  模式: 正常

## 规则

- ✅ **/t-sync-data**: 唯一允许网络的 skill (Tushare/datacenter API)
- ❌ **其他 7 个 skill**: 严格 0 网络, 全部走 DataStore (本地 parquet)
- ✅ **白名单工具**: `tools/storage/sync.py` + `tools/storage/sources/*`

## 扫描结果

- ✅ 允许: **11** 处
- ⚠️ 警告: **0** 处 (subprocess curl 等)
- ❌ 违规: **0** 处 (0 网络 skill 内有网络)

## ✅ 允许的网络入口 (白名单)

- `tools/storage/sources/eastmoney.py` — datacenter EPS 一致预期 (sync.py 调用)
- `tools/storage/sources/tushare.py` — Tushare 数据源 (sync.py 调用)
