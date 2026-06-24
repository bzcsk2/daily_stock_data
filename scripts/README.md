# scripts

这里放实际采集逻辑和共享 Python 模块。

English summary: Python collectors and shared modules.

## 采集脚本

| 脚本 | 用途 |
| --- | --- |
| `get_new_daily.py` | 增量同步日线。股票优先 Tushare、TickFlow，失败回退 baostock；指数使用 baostock。 |
| `get_new_5min.py` | 增量同步 5 分钟 K 线，并过滤异常交易时段外 bar。 |
| `download_quotes_tencent.py` | 交易时段循环采集腾讯市场快照。 |
| `sync_tushare_stock_basic.py` | 全量刷新 Tushare `stock_basic`。 |
| `sync_tickflow_instruments.py` | 同步 TickFlow instruments，补充上市日期、股本、涨跌停等字段。 |
| `sync_tdx_xdxr.py` | 同步 pytdx 除权除息和股本变化事件。 |
| `sync_tdx_finance.py` | 同步 pytdx 财务快照。 |
| `sync_tdx_blocks.py` | 同步通达信概念、指数、风格板块及成分关系。 |
| `sync_tdx_tick_trades.py` | 同步当日或历史逐笔成交。 |
| `backfill_tdx_tick_trades.py` | 按月向过去回填历史逐笔成交，并维护进度文件。 |
| `sync_tdx_f10_sections.py` | 按 daily/weekly/biweekly 分组同步 pytdx F10 章节。 |
| `export_tdx_f10_txts.py` | 导出每只股票完整 F10 文本。 |

## 共享模块

| 模块 | 用途 |
| --- | --- |
| `storage_common.py` | CSV/PostgreSQL 存储开关、CSV upsert、CSV 区间替换和 `.env` 加载。 |
| `kline_common.py` | baostock 交易日、股票清单、指数清单、数据库连接和通用日志。 |
| `snapshot_unified_common.py` | PostgreSQL 行情快照统一表结构和分区管理。 |
| `tdx_common.py` | pytdx 连接、通达信股票 universe 和代码转换。 |
| `tdx_f10_common.py` | pytdx F10 章节定义、文本抓取和哈希工具。 |
