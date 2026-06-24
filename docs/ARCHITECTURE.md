# 架构说明

English summary: The repository is organized as shell wrappers, Python collectors, and shared storage/provider helpers.

## 分层

- `bin/`：面向 cron 和手工运行的 shell 包装器
- `scripts/`：采集脚本和共享 Python 模块
- `docs/`：架构、运维、脚本用途说明

## 存储层

`scripts/storage_common.py` 负责公开存储开关：

- `STORAGE_BACKEND=csv`：写入 `DATA_DIR` 下的 CSV 文件
- `STORAGE_BACKEND=postgres`：写入 PostgreSQL 表
- `STORAGE_BACKEND=both`：同一轮任务同时写 CSV 和 PostgreSQL

`scripts/kline_common.py` 负责 A 股代码范围、指数清单、baostock 交易日、股票 universe、PostgreSQL 连接配置和日志初始化。

## 采集层

- K 线：`scripts/get_new_daily.py`、`scripts/get_new_5min.py`
- 快照：`scripts/download_quotes_tencent.py`
- 基础资料：`scripts/sync_tushare_stock_basic.py`、`scripts/sync_tickflow_instruments.py`
- pytdx 参考数据：`scripts/sync_tdx_xdxr.py`、`scripts/sync_tdx_finance.py`、`scripts/sync_tdx_blocks.py`
- pytdx 逐笔/F10：`scripts/sync_tdx_tick_trades.py`、`scripts/sync_tdx_f10_sections.py`、`scripts/export_tdx_f10_txts.py`

## 运行入口

`bin/run_*.sh` 是推荐入口。它们会自动：

- 从 `bin/` 回到项目根目录
- 读取项目根目录 `.env`
- 创建 `logs/`
- 设置 `PYTHONPATH` 到 `scripts/`
- 调用对应 Python 脚本

## 开源边界

仓库不包含生成数据、日志、私有数据库状态、provider token 或完整 F10 导出正文。用户自行配置 `DATA_DIR` 或 PostgreSQL 实例。
