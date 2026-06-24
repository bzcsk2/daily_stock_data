# 脚本用途索引

English summary: This page lists every shell wrapper and Python script in the repository and explains what each one does.

## Shell 包装脚本

这些脚本位于 `bin/`，用于 cron 或手工运行。它们会自动回到项目根目录、读取 `.env`、创建 `logs/`，并设置 `PYTHONPATH` 指向 `scripts/`。

| 脚本 | 用途 |
| --- | --- |
| `bin/run_daily_sync_batches.sh` | 分批同步 A 股股票和主要指数日线数据，调用 `scripts/get_new_daily.py`。 |
| `bin/run_5min_sync_batches.sh` | 分批同步 A 股股票和主要指数 5 分钟 K 线，调用 `scripts/get_new_5min.py`。 |
| `bin/run_tencent_snapshots.sh` | 在交易时段采集腾讯行情快照，内部带 flock，避免重复启动。 |
| `bin/run_tushare_stock_basic_sync.sh` | 同步 Tushare `stock_basic` 股票基础资料，需要 `TUSHARE_TOKEN`。 |
| `bin/run_tickflow_instruments_sync.sh` | 同步 TickFlow instruments 基础资料，需要 `TICKFLOW_API_KEY`。 |
| `bin/run_tdx_xdxr_sync.sh` | 同步 pytdx 除权除息、送转配股、股本变化等事件数据。 |
| `bin/run_tdx_finance_sync.sh` | 同步 pytdx 财务快照。 |
| `bin/run_tdx_blocks_sync.sh` | 同步 pytdx 通达信板块定义和成分股映射。 |
| `bin/run_tdx_tick_trades_am.sh` | 交易日上午收盘后同步上午逐笔成交。 |
| `bin/run_tdx_tick_trades_pm.sh` | 交易日下午收盘后同步下午逐笔成交，并可顺手补上午数据。 |
| `bin/run_tdx_tick_trades_backfill.sh` | 按日期区间回填历史逐笔成交。 |
| `bin/run_tdx_f10_daily_sync.sh` | 同步高频变化的 pytdx F10 章节。 |
| `bin/run_tdx_f10_weekly_sync.sh` | 同步中频变化的 pytdx F10 章节。 |
| `bin/run_tdx_f10_biweekly_sync.sh` | 同步低频变化的 pytdx F10 章节。 |
| `bin/run_tdx_f10_export.sh` | 导出完整 F10 文本到 `DATA_DIR/finance` 或 `TDX_F10_OUTPUT_DIR`。 |

## Python 采集脚本

这些脚本位于 `scripts/`，是实际采集和写入逻辑。

| 脚本 | 用途 |
| --- | --- |
| `scripts/get_new_daily.py` | 增量同步日线。股票优先尝试 Tushare、TickFlow，再回退 baostock；指数使用 baostock。 |
| `scripts/get_new_5min.py` | 增量同步 5 分钟 K 线，并过滤异常交易时段外 bar。 |
| `scripts/download_quotes_tencent.py` | 交易时段循环采集腾讯市场快照，写入统一快照表或 CSV。 |
| `scripts/sync_tushare_stock_basic.py` | 全量刷新 Tushare `stock_basic`。 |
| `scripts/sync_tickflow_instruments.py` | 同步 TickFlow instruments，补充上市日期、股本、涨跌停等字段。 |
| `scripts/sync_tdx_xdxr.py` | 同步 pytdx 除权除息和股本变化事件。 |
| `scripts/sync_tdx_finance.py` | 同步 pytdx 财务快照。 |
| `scripts/sync_tdx_blocks.py` | 同步通达信概念、指数、风格板块及成分关系。 |
| `scripts/sync_tdx_tick_trades.py` | 同步当日或历史逐笔成交。 |
| `scripts/backfill_tdx_tick_trades.py` | 按月向过去回填 pytdx 历史逐笔成交，并维护进度文件。 |
| `scripts/sync_tdx_f10_sections.py` | 按 daily/weekly/biweekly 分组同步 pytdx F10 章节。 |
| `scripts/export_tdx_f10_txts.py` | 导出每只股票完整 F10 文本。 |

## Python 共享模块

| 模块 | 用途 |
| --- | --- |
| `scripts/storage_common.py` | CSV/PostgreSQL 存储开关、CSV upsert、CSV 区间替换和 `.env` 加载。 |
| `scripts/kline_common.py` | baostock 交易日、股票清单、指数清单、数据库连接和通用日志。 |
| `scripts/snapshot_unified_common.py` | PostgreSQL 行情快照统一表结构和分区管理。 |
| `scripts/tdx_common.py` | pytdx 连接、通达信股票 universe 和代码转换。 |
| `scripts/tdx_f10_common.py` | pytdx F10 章节定义、文本抓取和哈希工具。 |
