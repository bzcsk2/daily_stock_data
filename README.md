# daily_stock_data

[![CI](https://github.com/bzcsk2/daily_stock_data/actions/workflows/ci.yml/badge.svg)](https://github.com/bzcsk2/daily_stock_data/actions/workflows/ci.yml)

A 股数据采集脚本集合，覆盖日线、5 分钟线、腾讯行情快照、Tushare/TickFlow 基础资料，以及 pytdx 参考数据。项目默认写 CSV，不需要数据库；需要长期运行或 SQL 查询时，也可以写 PostgreSQL，或者 CSV 和 PostgreSQL 双写。

English summary: A-share market data collectors with CSV and PostgreSQL storage for daily bars, 5-minute bars, snapshots, metadata, and pytdx reference data.

## 项目状态

当前是早期开源版本，代码来自一套实际运行的个人采集流程，并为开源使用补了 CSV 存储模式。仓库不包含个人数据库、数据库 dump、密钥、日志、运行数据或完整 F10 导出正文。

## 支持环境

- Python 3.11+
- Linux/macOS/WSL shell 环境
- cron 示例默认按 `Asia/Shanghai` 市场时区运行

## 目录结构

```text
bin/      cron 和手工运行用的 shell 包装脚本
scripts/ 具体采集脚本和共享 Python 模块
docs/    架构、运维、脚本用途、数据契约和数据源说明
tests/   当前主要覆盖 CSV 存储语义
```

详细脚本用途见：[脚本用途索引](docs/SCRIPTS.md)。

## 存储模式

- `csv`：写入 `DATA_DIR` 下的本地 CSV 文件，默认模式，不需要数据库
- `postgres`：只写 PostgreSQL
- `both`：同一轮任务同时写 CSV 和 PostgreSQL

CSV 适合试用、日线和基础资料。5 分钟、逐笔、F10、交易时段快照等长期高频任务建议使用 PostgreSQL；CSV 模式会随文件变大出现明显 IO 成本。

## 快速开始

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 默认 STORAGE_BACKEND=csv，可直接运行

./bin/run_tushare_stock_basic_sync.sh
./bin/run_daily_sync_batches.sh
```

默认 CSV 输出示例：

- `data/stock_basic_tushare.csv`
- `data/daily_ohlcv.csv`
- `data/index_daily.csv`
- `data/min5_ohlcv.csv`
- `data/quote_snapshots_unified.csv`

## 主要采集任务

- 日线 OHLCV：`scripts/get_new_daily.py`
- 5 分钟 OHLCV：`scripts/get_new_5min.py`
- 腾讯行情快照：`scripts/download_quotes_tencent.py`
- Tushare 股票基础资料：`scripts/sync_tushare_stock_basic.py`
- TickFlow instruments 基础资料：`scripts/sync_tickflow_instruments.py`
- pytdx 除权除息/股本变化：`scripts/sync_tdx_xdxr.py`
- pytdx 财务快照：`scripts/sync_tdx_finance.py`
- pytdx 板块成分：`scripts/sync_tdx_blocks.py`
- pytdx 逐笔成交：`scripts/sync_tdx_tick_trades.py`
- pytdx F10 章节表和全文导出：`scripts/sync_tdx_f10_sections.py`、`scripts/export_tdx_f10_txts.py`

## PostgreSQL

在 `.env` 中设置：

```env
STORAGE_BACKEND=postgres
MARKET_DB_HOST=localhost
MARKET_DB_PORT=5432
MARKET_DB_NAME=market
MARKET_DB_USER=postgres
MARKET_DB_PASSWORD=
```

如果希望保留 CSV 文件同时写库，使用：

```env
STORAGE_BACKEND=both
```

脚本会在需要时自动创建或更新对应 PostgreSQL 表结构。

## 数据源凭证

- `TUSHARE_TOKEN`：启用 Tushare 日线补充源和 stock_basic
- `TICKFLOW_API_KEY`：启用 TickFlow 日线补充源和 instruments
- baostock、腾讯/easyquotation、pytdx 相关任务不需要项目级密钥

数据源、降级顺序、频率限制和复权语义见：[数据源说明](docs/DATA_SOURCES.md)。

## 数据契约

生成的 CSV/PostgreSQL 表字段、主键和规模边界见：[数据契约](docs/SCHEMAS.md)。

## 定时任务

复制 `cron.example`，把 `/path/to/daily_stock_data` 改成你的仓库路径，然后用 `crontab -e` 安装需要的任务。示例 cron 显式使用 `CRON_TZ=Asia/Shanghai`。

`bin/run_*.sh` 包装器会：

- 自动回到项目根目录运行
- 自动读取 `.env`
- 自动创建 `logs/`
- 支持 `PYTHON_BIN=/path/to/python` 覆盖 Python 解释器

## 开发检查

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
ruff check . --select E9,F63,F7,F82
ruff check scripts/storage_common.py scripts/tdx_common.py scripts/kline_common.py scripts/snapshot_unified_common.py scripts/tdx_f10_common.py
ruff check scripts/get_new_daily.py scripts/get_new_5min.py
ruff check scripts/sync_tushare_stock_basic.py scripts/sync_tickflow_instruments.py
ruff check scripts/download_quotes_tencent.py
ruff check scripts/sync_tdx_xdxr.py
ruff check scripts/sync_tdx_finance.py
ruff check scripts/sync_tdx_blocks.py
ruff check scripts/sync_tdx_tick_trades.py scripts/export_tdx_f10_txts.py
ruff check scripts/sync_tdx_f10_sections.py
python -m py_compile scripts/*.py
pytest
for script in bin/run_*.sh; do bash -n "$script"; done
```

## 文档

- [脚本用途索引](docs/SCRIPTS.md)
- [架构说明](docs/ARCHITECTURE.md)
- [运维说明](docs/OPERATIONS.md)
- [数据契约](docs/SCHEMAS.md)
- [数据源说明](docs/DATA_SOURCES.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- [变更记录](CHANGELOG.md)

## 免责声明

本项目是数据采集工具，不构成投资建议。运行定时采集前，请自行确认各上游数据源的服务条款、频率限制和合规要求。
