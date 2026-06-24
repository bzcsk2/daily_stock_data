# bin

这里放 cron 和手工运行用的 shell 包装脚本。每个脚本会自动回到项目根目录、读取 `.env`、创建 `logs/`，并设置 `PYTHONPATH` 指向 `scripts/`。

English summary: Shell wrappers for cron and manual operations.

| 脚本 | 用途 |
| --- | --- |
| `run_daily_sync_batches.sh` | 分批同步 A 股股票和主要指数日线数据。 |
| `run_5min_sync_batches.sh` | 分批同步 A 股股票和主要指数 5 分钟 K 线。 |
| `run_tencent_snapshots.sh` | 交易时段采集腾讯行情快照，内部带 flock。 |
| `run_tushare_stock_basic_sync.sh` | 同步 Tushare `stock_basic` 股票基础资料。 |
| `run_tickflow_instruments_sync.sh` | 同步 TickFlow instruments 基础资料。 |
| `run_tdx_xdxr_sync.sh` | 同步 pytdx 除权除息和股本变化事件。 |
| `run_tdx_finance_sync.sh` | 同步 pytdx 财务快照。 |
| `run_tdx_blocks_sync.sh` | 同步 pytdx 板块定义和成分股映射。 |
| `run_tdx_tick_trades_am.sh` | 同步上午逐笔成交。 |
| `run_tdx_tick_trades_pm.sh` | 同步下午逐笔成交，并可补上午数据。 |
| `run_tdx_tick_trades_backfill.sh` | 回填历史逐笔成交。 |
| `run_tdx_f10_daily_sync.sh` | 同步高频变化的 F10 章节。 |
| `run_tdx_f10_weekly_sync.sh` | 同步中频变化的 F10 章节。 |
| `run_tdx_f10_biweekly_sync.sh` | 同步低频变化的 F10 章节。 |
| `run_tdx_f10_export.sh` | 导出完整 F10 文本。 |
