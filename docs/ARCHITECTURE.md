# Architecture

The project is organized as standalone collectors plus shared helpers.

## Storage

`storage_common.py` owns the public storage switch:

- `STORAGE_BACKEND=csv`: write CSV files under `DATA_DIR`
- `STORAGE_BACKEND=postgres`: write PostgreSQL tables
- `STORAGE_BACKEND=both`: write both in the same run

`kline_common.py` owns shared market symbols, baostock helpers, PostgreSQL
connection settings, and fallback symbol loading.

## Collector Groups

- K-line collectors: `get_new_daily.py`, `get_new_5min.py`
- Snapshot collector: `download_quotes_tencent.py`
- Metadata collectors: `sync_tushare_stock_basic.py`, `sync_tickflow_instruments.py`
- pytdx reference collectors: `sync_tdx_xdxr.py`, `sync_tdx_finance.py`, `sync_tdx_blocks.py`
- pytdx tick/F10 collectors: `sync_tdx_tick_trades.py`, `sync_tdx_f10_sections.py`, `export_tdx_f10_txts.py`

The `run_*.sh` wrappers are operational entry points for cron and manual runs.
They load `.env`, create `logs/`, and allow `PYTHON_BIN` overrides.

## Open Source Boundary

The repository intentionally excludes generated data, logs, private database
state, and provider credentials. Users bring their own data directory or
PostgreSQL instance.
