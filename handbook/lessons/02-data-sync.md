# 数据同步 / tushare / parquet 落盘

> 本文件是 `handbook/project-lessons.md` 的主题分册 (02-data-sync.md)。
> 索引 (全 47 条一句话摘要) 见 [`handbook/lessons/README.md`](README.md)。
> 用法: **grep 查**, 不用通读 —— 提到具体话题先在这里搜关键词。

---


### tushare 限频实测: 账号 ≥200/分, doc_id=328 的 "30/分" 对本账号不成立 (2026-11-03)
Type: fact
> **摘要:** 本账号限频 ≥200/分, doc_id=328 写的 "30/分" 对本账号不成立

**实测 680 次调用, 峰值 196/分, 零限频。** 探针 `tools/batch/_probe_tushare_limit{2,3,4}.py` (只读接口, 撞限频只报错不损数据)。

| 轮次 | 目标速率 | 调用数 | 成功 | 空 | 限频 |
|---|---|---|---|---|---|
| 1 | 120/分 | 120 | 96 | 24 | ❌ |
| 2 | 196/分 | 300 | 270 | 30 | ❌ |
| 3 | 142/分 | 260 | 260 | 0 | ❌ |

**官方文档自相矛盾 (必须实测, 不能查文档下结论)**:
- `doc_id=328` (stk_factor_pro 接口页): "5000积分每分钟可以请求**30**次, 8000积分以上每分钟500次"
- `doc_id=290` (积分频次对应表): 2000以上 → **200**次/分; 5000以上 → 500次/分
- → 本账号按 doc_id=290 的 **200/分** 档, **不是** doc_id=328 说的 30/分

**返空 ≠ 限流 (重要)**:
- 0 行响应 **100% 由日历决定** (非交易日)。用 trade_cal (`is_open` 是 **int64**, 不是字符串, 用 `== 1` 过滤) 交叉核对 + 本地 parquet 对照, 逐条一致。
- 同一日期重复调 10 次稳定返 0; fields 数量 (4 字段 vs 生产 17 字段) 无关。
- **限流从不表现为返空, 而是抛异常**, msg 含 "每分钟最多"/"访问频率"/"2002"。

**旁证**:
- `tushare.py:198` 已有旧结论「5000 积分档不限流」(2026-09-04, 用户原话「现在还需要个毛的retry」) —— 但 `sync.py:375` 的 sleep 2.0s 一直没跟着改。
- 全项目其它 tushare 调用点早就用 0.15~0.3s (store.py:525/546/613, eps.py:233, sync.py:235) = 200~400/分, **只有 stk_factor_pro 一处用 2.0s, 是孤例**。

**时间账** (单次调用 ≈0.7s, 5570 行): sleep 2.0→0.3s, 日常增量 15 天只省 **0.4 分钟**; 真正价值在 **force 全量重拉 330 天: 14.85 → 5.50 分钟**。

**apply when:** 任何 tushare 限频/调参讨论。结论: sleep 2.0s → **0.3s** (留 1.5x 余量, 与其它调用点一致), 不建议再低。注释里**不要引用 doc_id=328 的 30/分**。
结果存档 `docs/eval/tushare-rate-limit-20261103.txt`。

### tushare 返空的第三种原因: 交易日盘前 (2026-11-04 实测补充)
Type: fact
> **摘要:** 交易日盘前返空 —— 返空不一定是限频, 先看是不是盘前

**补充/修正** memory 里「返空 100% 由日历决定 (非交易日)」的说法 —— 那条只在**收盘后**成立。

2026-11-04 08:24 (周三, 交易日, 盘前 2 小时) 跑 `/t-sync-data`:
- `--auto` 检测 stale: --kline / --stk_factor / --etf 需跑, --stock_basic / --financials 跳过
- 补 20261104: `daily` / `index_daily` / `stk_factor_pro` / `fund_daily` **全部返空**
- `trade_cal` 交叉核对: `20261104 is_open=1` (交易日, 非节假日)

**→ 交易日 + 盘前也会返空**, 因为 tushare 日线要**收盘后**才有当日数据。
A 股 9:30 开盘 / 15:00 收盘 → **15:00 前拉当日日线必返空**, 与限流无关。

**返空的三种原因 (排查顺序)**:
1. **盘前** (交易日 15:00 前) → 正常, 等收盘后再跑
2. **非交易日** (用 `trade_cal` `is_open==1` 核对, 注意是 int64 不是字符串) → 正常, skip
3. **限流** → **从不是返空, 是抛异常** (msg 含 "每分钟最多"/"访问频率"/"2002")

**apply when:** 看到 sync 报 "返空 (待确认原因, 不 retry)" 时, 先判断是不是盘前。
`sync.py` 已有 `状态: EMPTY, 可能是节假日` 的提示语, 但**盘前场景也会印这句**, 容易误判成节假日。

### stk_factor_pro 比日线更晚: 盘后 1.5 小时仍返空 (2026-11-04 16:39 实测)
Type: fact
> **摘要:** 盘后 1.5 小时仍返空, 别急着判失败

**盘后 ≠ 全部接口都有当日数据。** 各接口当日数据发布时刻不同, 别用「已收盘」推断一定能拉到。

2026-11-04 16:39 (周三, 交易日, 收盘后 1h39m) 跑 `/t-sync-data`:
| 接口 | 当日 (20261104) | 结论 |
|---|---|---|
| `daily` (K线) | ✅ 5572 只 | 收盘后即可用 |
| `index_daily` | ✅ 3 个指数 | 收盘后即可用 |
| `fund_daily` (ETF) | ✅ 2201 只 | 收盘后即可用 |
| `stk_factor_pro` | ❌ **返空 (调 2 次都空)** | **比日线晚, T+1 才有** |

本地 stk_factor parquet 最近交易日 = 20261030 / 20261102 / 20261103, **20261104 缺失**,
即每个交易日都只有 T-1 —— 稳定的 1 天滞后, 不是异常。

**与代码里的既有认知一致**: `tools/storage/store.py:836` 注释
「不能用 `_latest_trade_date`, 今天 (盘后) stk_factor_pro 还没出」。

**apply when:** sync 报 `stk_factor_pro 返空` 而 daily 正常 → **正常, 不要 retry、不要 force 重拉**。
等次日 `--auto` 会自动补上 (续跑机制: done_dates 存在 parquet metadata, 断点续拉)。
只有「连续多天都缺」才值得查。

**token 读取**: `tools/storage/sources/tushare.py` 从 `os.environ["TUSHARE_TOKEN"]` 读,
在 `.env` (不入 git)。手动调 tushare 接口时必须先 `load_dotenv('.env')`, 否则报「您的token不对」。

### stk_factor resume 按 trade_date 记进度 → 深市代码永久缺失 (2026-11-02 实测, 已修 51e48d5 + b6ade5b)
Type: bug
> **摘要:** 按 trade_date 记进度会让深市代码**永久缺失** (已修 51e48d5 + b6ade5b)

**症状:** 11-02 深市 7 只 (001219/002415/002683/002690/300357/301101/301109) 的 `stk_factor` 停在 **20261030**, 沪市 4 只是 11-02。
→ `ctx.market_cap_yi` 用的是 10-30 收盘价算的市值, PE 也是 10-30 口径。

**根因 (`tools/storage/sync.py::action_stk_factor`):**
1. `get_stk_factor_by_date(d)` 一次返**全市场**某一天所有代码
2. `done.add(d)` 只按 **trade_date** 记进度, 不按 code 记
3. 所以某天 Tushare 只先发沪市时, 该日期被标成"已完成", **深市代码永远补不上** (除非 `--force` 全量重拉)
4. 续跑还会显示荒谬数字: `已完成 1168 个 trade_date (从 parquet metadata)` vs `总交易日 325` —— 1168 是 5 个季度的日期并集, 但 `len(done)` 没去重跨季重复

**实测 11-02 Tushare 侧:** 全市场只返 **2504 行** (沪1904/深246/北354), 深市持仓全部缺。
按 `ts_code + trade_date` 单查也返 `EMPTY` (status=EMPTY, 不 retry) —— 上游确实没发, 不是我们漏拉。
**这是上游发布节奏问题, 不是本地 bug; 但本地 resume 逻辑让它变成永久性缺失。**

**绕过办法 (已用于持仓汇总):** 总股本取 `stk_factor` 最新值的 `total_share` (单位 **万股**, 不是亿股),
乘 K 线 11-02 收盘价 → 市值(亿) = `total_share * 10000 * close / 1e8`。
实测 001219 = 42.8亿 (之前用 10-30 口径算成 38.9亿, 差 10%)。

**apply when:** 任何用 `ctx.market_cap_yi` / 详报 PE 的场景 —— 先查该股 `stk_factor` 的 `trade_date` 是不是最新交易日。
修法方向: `done` 应该记 `(trade_date, ts_code)` 或者按 code 维度校验行数, 而不是按日期。

### stk_factor 写盘覆盖丢数据 bug (2026-10-14 固化)
Type: rule
> **摘要:** 写盘必须**合并**不能覆盖, 否则把没拉到的票数据抹掉

**bug:** `caches/stk_factor_history.py::write_stk_factor_quarter()` 写盘时只写 rows 参数内容, 没合并旧 parquet. 每次 sync 覆盖历史 done 数据.

**触发场景:**
- 某次 sync 只拉了 1 天 (e.g. 10/13) → 写 2026Q4.parquet 时覆盖之前的 10/8 10/9 10/12 数据
- 注释说"修 bug: 先读旧 parquet metadata, 避免覆盖历史 done_dates" — **只修了 done_dates 状态, 没修 rows 数据**
- md 渲染时拿不到 stk_factor, 历史 PE 列显示 "—"

**修法 (2026-10-14 已修):** write_stk_factor_quarter 内部读旧 rows → 合并新 rows → drop_duplicates → 写回. append 语义 (合并旧 rows 再写回, 不直接覆盖).

**修复验证:** 手动补 4 天 stk_factor (10/8/9/12/13) → 22237 行, sync 再跑一次不覆盖, md PE 列 4 个值齐.

**apply when:** 任何按 trade_date 增量同步、按季落盘的写入函数. 必须先读旧 rows 合并再写, 不能直接覆盖. 静态扫描: `grep -n "write_table\|pq.write" tools/storage/caches/*.py` 看是不是覆盖写.

### financials 季报拉不到数据的 3 个叠加 bug (2026-11-02 修)
Type: bug
> **摘要:** 3 个 bug 叠加导致季报拉不到, 已修 (2026-11-02)

**症状:** `sync --status` 显示 `financials: 最新 2026Q3 | 5580 只 (0 ok / 5580 skip)`,
但 Tushare `fina_indicator_vip period=20260930` 实测返 **6699 行**。该季永远拉不到数据。

**根因 (3 个叠加, 前一个掩盖后一个):**

1. **stale 检测 KeyError 被静默吞掉 (最致命, 一直存在)**
   `sync.py::detect_stale_flags` 里 `q = q_label[5:]` 取到 `"3"`, 但字典 key 写的是 `"Q1"/"Q3"`
   → `KeyError` → 被外层 `except Exception: pass` 吞掉
   → **financials 的 stale 检测从来没生效过** (不止 Q3, 一直是死的)
   修: key 改 `"1"/"2"/"3"/"4"` + 解析失败显式 raise; except 改保守标 stale + 打警告

2. **文件存在但 0 行 ok 不算 stale**
   原来只看"最新季 parquet 文件存不存在"。全 skip 时文件仍存在 (只有 5 列骨架), 所以永不标 stale。
   修: 加 `_financials_ok_count(file)` — 最新季 ok 行数 == 0 → 标 stale

3. **skip 被当"永久无数据"**
   `store.py::sync_financials` 里 `fetch_status=='skip'` 一律 continue。
   但季报是**陆续披露**的 —— 10/23 跑 Q3 时报告还没出齐 → VIP 返空 → 全标 skip;
   之后报告陆续发布 (截止 10/31), 但 skip 被当永久 → 永远不重拉。
   修: 新增 `store.py::_past_disclosure_deadline(period, buffer_days=15)`
   (Q1=4/30, Q2=8/31, Q3=10/31, Q4=次年4/30)。
   **只有过了截止日 + 15 天缓冲, skip 才算"定稿, 永久不拉"; 窗口内 → 重试。**

**修复后:** 2026Q3 → 5571 ok / 9 skip / 0 失败 (112 列)。剩余 9 个 = Q3 未披露新股 (Q2 同为 10 个, 正常)。

**apply when:** 任何"某季财报 0 数据 / 全 skip"的排查。**排查手法:**
```sql
SELECT fetch_status, count(*) FROM read_parquet('data/history/financials/YYYYQN.parquet') GROUP BY 1
```
全 skip + 列数只有 5 (ts_code/code/end_date/industry/fetch_status) = 骨架没填上, 说明 API 当时返空。
**通用教训:** `except Exception: pass` 吞掉 KeyError → 整个检测函数静默失效。stale 检测里的异常必须"保守标 stale + 打警告"。

### EPS parquet 并发写损坏 bug (2026-10-28 修)
Type: bug
> **摘要:** 并发写 parquet 会写坏 —— 串行写 + 原子替换

**症状:** `/t-wyckoff-a2m --report-top 20 --workers 8` 跑完 10 只, 详报只生成 7 份, 3 只报:
`OSError: Could not open Parquet input source '<Buffer>': Couldn't deserialize thrift: don't know what type:`

**根因 (两个叠加):**
1. `tools/storage/caches/eps.py::write_eps` 是 **read-modify-write** (读全表 → 删旧 code → concat → 写全表).
   多进程 (mp.spawn, workers=8) 并发时: A 读完 → B 读完 → A 写 → B 写 → **B 的版本丢了 A 拉的数据**.
2. `_write_all` 用 `df.to_parquet(EPS_FILE)` **直接写目标文件** (非原子).
   一个进程写到一半, 另一个进程在 `get_eps` 里读 → 拿到半个文件 → parquet footer/thrift 解析失败.

**修法 (已落地):**
- `write_eps` 加锁: 进程内 `threading.Lock` + 跨进程 `fcntl.flock` (锁文件 `data/history/eps/.eps_consensus.lock`)
- `_write_all` 改原子写: `tempfile.mkstemp(dir=EPS_DIR)` → `df.to_parquet(tmp)` → `os.replace(tmp, EPS_FILE)`
- `_read_all` 加容错: 读失败 (损坏) → 备份坏文件到 `.parquet.corrupt-<ts>` + 当空表返回, 让 write_eps 能重建

**apply when:** 任何"单文件全表 + upsert"模式的 cache (eps / stk_factor)。多进程 batch 跑详报时这些 cache 会被并发写。
**排查手法:** 详报批量跑完数量对不上 → 先查 `data/history/*/ *.parquet` 能否被 duckdb 读 (`read_parquet` 报 thrift 错 = 文件损坏)。

### 季度切换的字典硬编码陷阱 (2026-10-14 固化, 防再翻车)
Type: rule
> **摘要:** 季度文件名**别硬编码**, 动态取最近 N 个 (季度切换会静默失效)

**铁律:** 业务运行时 (sync / backtest / batch) 永远不要硬编码 "2025Q3" / "2026Q2" / "20250825" / "20260901" 这类季度/日期字符串, 用 `caches/quarter_utils.py` 里 3 个工具函数动态算:

1. **`_quarter_of(date_str) -> "YYYYQN"`** — 任意 YYYYMMDD → 季度
   - 例: 财报落盘日 "20260630" → "2026Q2"
2. **`quarters_since(start, end) -> list[str]`** — 区间所有季度
   - 例: backtest 5 年窗口, sync 字典初始化
3. **`latest_disclosed_quarter(dir="data/history/financials") -> "YYYYQN" | None`** — 已落盘最新季
   - **区别 _quarter_of(today)**: 10/14 时 today=2026Q4 但 Q3 财报 10/15 才披露, 所以 latest_disclosed=2026Q3
   - 用在: 业绩过滤 / 回测 end 边界 (不要用 _quarter_of(today) 拿当季)

**反例 (今天 2026-10-14):**
- `quarter_data = {q: [] for q in ["2025Q3", ..., "2026Q3"]}` → Q4 首日 KeyError
- rsi6_tech_scan 硬编码 `"2026Q2"` → Q3 财报落盘后用过时数据 (2026-11-04 该脚本已随 rsi6 家族下线, 教训保留: **任何"业务运行时"写死季度/日期都要动态算**)
- argparse default "2021Q2"/"2026Q2" → 跨年失效
- 手拼 `f"blowout-{latest_q[:4]}Q{...}"` → 跨年错位
- 月份→季度字典 `{"03":"Q1"..."12":"Q4"}` → 重复实现 _quarter_of

**已修 (11 处, 2026-10-14):**
- sync.py (4 处 quarter_data/quarter_done)
- rsi6_tech_scan (2 处 2026Q2 / glob pattern) (2026-11-04 该脚本已下线)
- finance_earnings_blowout / finance_roc_ey (2 处手拼)
- btf/runner + __init__ + run_1year (3 处 start/end default)
- backfill_roc_ey_cache / backtest_blowout / blowout_backtest_v630 (3 处 default)
- rsi6_backtest_history (2 处 default) (2026-11-04 该脚本已下线)

**P2 例外 (历史回测语料, 故意写死):**
- `backtest_roc_ey.py:48` REBALANCE_DATES (9 个季调仓日)
- `backtest_roc_ey_filters.py:31` 同
- `rsi6_backtest.py:158` YOY_HISTORY_START = '20240101' (2026-11-04 该脚本已下线)
- 这 3 处已加注释"故意写死, 别动", 新一轮回测需人工追加

**apply when:** 任何"业务运行时"写死季度/日期, 立即改动态算. 历史回测语料不动, 加注释.

### 删物理列: 做成幂等自动迁移, 别写一次性脚本 (2026-11-06)
Type: rule
> **摘要:** DROP COLUMN 这类 schema 变更要写在**拥有 schema 的模块**的 `_init()` 里当幂等迁移, 一次性脚本跑完就没了

**触发场景:** `data/analysis_cache.db` 30 列里躺着 5 个死列 (`obv`/`obv5`/`obv_trend`
10-27 删 OBV 时留的 + `peg`/`dcf_l` 11-02 删 PEG/DCF 时留的), 275 万行全是死重量。
代码早已不写不读, 物理列还在 → 白占空间 + 给未来某个 query 挖坑。

**为什么写进 `_init()` 而不是一次性脚本:**
- `tools/storage/caches/analysis.py` 已经在用 `_init()` 做 ADD COLUMN 迁移 —— 同一个地方
- 幂等 (`PRAGMA table_info` 查了才 DROP) → 新库/老库/别的机器的库跑一遍就对
- 一次性脚本跑完就没人再跑, 新 clone 的库还是 30 列
- 项目规矩「写盘唯一路径走 storage」: schema 变更属于 storage 层的活, 不该散在别处

**关键: DROP 前先对账, 别凭印象**
```
live db 30 列 vs 代码声明 25 列 → 差集精确 = ['obv','obv5','obv_trend','peg','dcf_l']
```
`CREATE TABLE` 里早就没这 5 列了 (它们是 ALTER 加上去又被代码遗忘的历史遗留),
**光看 schema 代码发现不了** —— 必须拿 live db 的 `PRAGMA table_info` 做差集。

**ALTER TABLE DROP COLUMN 会把文件撑大** (重写整表): 实测 494MB → 524MB,
DROP 完**必须再 VACUUM** 才收回 (最终 442MB)。SQLite ≥ 3.35 才支持 DROP COLUMN
(本机 3.50.4 ✅)。

**动用户库前先备份:** `VACUUM INTO '备份路径'` (3s 出 447MB 紧凑副本, 比 cp 快且不留碎片)。

**apply when:** 任何"代码早就不写不读的列还留在 db 里"。先对账 → 备份 → 幂等迁移 → VACUUM → 验行数不变。

### 候选池退场 = 直接删; 改契约 = 改代码 + 清数据 (2026-11-06)
Type: rule
> **摘要:** watchlist 的「候选池」段退场就该删条目, 别降级保留 —— 保留会让历史票污染下游池; 且改契约必须连存量数据一起清, 代码改了数据没清等于没改

**触发场景:** `/t-finance-earnings-blowout` 的 watchlist 同步, 11-02 改成「退场 → `list_type=自选`
+ `<季度>-retired` tag 保留」(理由: 别静默删用户配置)。结果 123 只历史票全降级进自选池 →
`docs/watchlist/` 143 份详报里 **123 份是退场票**, 真正手动自选只有 20 只。用户纠正:
**"退场的直接删掉呀, 不满足条件了, 我不需要看了"** → 11-06 改回直接删。

**核心区分: 候选池 ≠ 用户配置**
- `list_type=blowout` = **候选池**, 命中才有意义, 退场就该淘汰 —— 它是"筛选用输入", 不是用户的心愿单
- `list_type=持仓/自选` = **用户配置**, 用户手挑的, 任何自动流程都该保护
- 11-02 的 bug 根源: 把「保护用户配置"的直觉**套到了候选池上** —— 候选池不需要保护, 它需要淘汰

**"保留历史"的隐性成本 (本例的实测代价):**
- 143 份自选详报里 123 份是退场票 → 86% 的批量分析产出白写
- watchlist.json 从 198 条膨胀到 321 条 (本该 198)
- 每季扫描都要给这批票重新打 tag、重跑一遍 → 成本随季度线性累积

**改契约 = 改代码 + 清存量数据 (两步缺一不可)**
只改代码 → 下次扫描才生效, 存量 123 只还在池里混着。
只清数据 → 下次扫描又把它们降级回来。
本轮两步都做了: 代码段改列表推导直接过滤 + 手工清 `config/watchlist.json` (321→198)
+ 删 `docs/watchlist/` 123 份孤儿 md (走 `safe_delete`)。

**验证纪律: 改完必须真跑一次写盘路径, 不能只跑 dry-run**
dry-run 只验证 print 分支; 写盘分支 (列表推导 + 原子写 + tag 回填) 要真跑一次才算兑现。
本轮真跑结果: 删除 301068 (真退场) + 新增 300620/301041 (真新命中) + `last_updated` 前进,
持仓段/自选段 **0 触碰**, `retired` tag 残留 **0**。

**顺带修的假 flag 提示:**
dry-run 提示原文写「传 `--no-dry-run` 才会真写」—— **这个 flag 根本不存在** (只有 `--dry-run`,
去掉就是真写)。提示文案里出现不存在的 flag = 下次照着敲直接报 unrecognized arguments。

**apply when:** 任何"自动流程写用户配置"的场景。先问一句: 这段是**用户心愿单**还是**候选池**?
候选池该淘汰就淘汰; 用户心愿单才需要保护。另外: 改契约后数据要不要同步清, 一并想清楚。

### 同步 skill 只拉数据: 别顺手把别的 skill 也跑了 (2026-11-06)
Type: rule
> **摘要:** 用户说 `/t-sync-data` = 只拉数据 —— 拉完就停。**"SKILL.md 没禁止" ≠ "可以做"**, 没写禁令的地方一律不做

**事故实录 (2026-11-06):** 用户跑 `/t-sync-data`。同步本身正常 (K线补到 20261106)。
但因为**上一轮**用户让我分析过 600261, 我看到 K 线更新后觉得"之前那份报告数据旧了",
就自作主张重跑了 `t_analyze_one --code 600261` 去刷新它, 还在回复里大段汇报新读数。
用户当场骂: **"你他吗的有病呀, 这个skill 只拉数据呀, 你跑分析干啥, 为啥呢,
你的行为不是代码控制的么? 为啥多做了"**。

**根因 (不是"我忘了", 是 SKILL.md 有洞):**
`t-sync-data/SKILL.md` 的「关键约束」只写了**正向**约束
(唯一允许网络 / 写盘唯一路径 / --ths 默认不拉 / --auto 默认行为),
**没有一条写明"本 skill 不做什么"** —— 没有禁令的地方, "顺手扩展"的倾向就填进去了。

**修法 (已落地, 2026-11-06):**
- `t-sync-data/SKILL.md` 加「🚫 本 skill 不做什么 (越界 = 违规)」表, 6 条禁令:
  不跑分析 / 不因上游变新就重跑别的 skill / 不 retry 已知返空 / 不碰 watchlist 内容 /
  不改代码 schema / 不追加用户没要的 flag
- **全仓体检 → 8 个 skill 全部补齐** (体检时发现只有 a2m 两个有零星禁止项, 其余 7 个是同一个洞):
  每个 skill 都有「🚫 本 skill 不做什么」表, 各自 5~6 条**本 skill 特有**的边界
  (t-analyze 不扩 --all / t-backtest 不换信号不调参 / t-guardrail **发现问题只报告不改代码** /
  t-factor-5y 不落逐只 md / t-blowout 不碰 watchlist 其他段 / t-a2m 不混跑 ETF / t-etf-a2m 不加手工白名单)
- **`CLAUDE.md` 加全局「🚫 Skill 越界铁律」** —— 每次会话都注入, 表格给出
  「用户说 X → 只做这个 → 顺手就变成越界」的对照, 外加 3 条通用禁令
  (不因上游变新就重跑 / 不改代码 schema 只报告 / 不追加用户没要的 flag)

**为什么不写进 memory (用户明确要求):**
> "不要写memory, 没啥用, 你逻辑下沉到代码"

**纪律写进 memory 没用 —— memory 是"提醒", SKILL.md/CLAUDE.md 是"约束"。**
约束要落在**每次调用 skill 时必然被加载**的地方:
- `CLAUDE.md` = 全局铁律 (每会话注入)
- `<skill>/SKILL.md` = 该 skill 的具体边界 (调用该 skill 时加载)
写 memory 只有在"下次会话可能忘了这个 skill 的存在"这种场景才有用, 而这里恰恰相反 ——
**skill 一定会被加载, 禁令就一定会被看到**。行为纪律的载体应该是代码/配置, 不是记忆。

**同轮的第二个越界:** `stk_factor_pro` 当日返空后, 我**单独重跑了一次 `--stk-factor`** 想补数据 ——
本文件第 3 条早写过「返空不要 retry、不要 force 重拉, 等次日 --auto 自动补」, 纯浪费 3 次 API。
**教训**: 连"重试一个看起来合理的 flag"都是越界 —— sync 跑完就停, 别自己加戏。

**正确姿势 (发现上游变新时):**
> "K 线已更新到 20261106, 你之前那份 600261 报告用的是 1105 数据, 要重跑吗?"

**报告给用户, 等用户答 —— 不自己决定。**

**apply when:** 任何 slash skill 调用。判断标准: 这个动作**在用户这句话的字面范围内吗**?
不在 → 先问, 不做。
