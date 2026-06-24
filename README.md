# daily_stock_data

[![CI](https://github.com/bzcsk2/daily_stock_data/actions/workflows/ci.yml/badge.svg)](https://github.com/bzcsk2/daily_stock_data/actions/workflows/ci.yml)

A-share daily data collection scripts for K-line data, Tencent snapshots,
Tushare/TickFlow stock metadata, and pytdx reference data.

Project status: early open-source release. The collectors are practical scripts
extracted from a working personal workflow, with CSV storage added as the
default public-friendly backend.

The project supports two storage modes:

- `csv`: write local CSV files under `DATA_DIR` (default, no database required)
- `postgres`: write PostgreSQL tables only
- `both`: write CSV and PostgreSQL at the same time

No private database dump, credentials, logs, or downloaded F10 text corpus is
included in this repository.

## Data Jobs

- Daily OHLCV: `get_new_daily.py`
- 5-minute OHLCV: `get_new_5min.py`
- Tencent market snapshots: `download_quotes_tencent.py`
- Tushare stock_basic: `sync_tushare_stock_basic.py`
- TickFlow instruments: `sync_tickflow_instruments.py`
- pytdx xdxr/share-change events: `sync_tdx_xdxr.py`
- pytdx finance snapshots: `sync_tdx_finance.py`
- pytdx block memberships: `sync_tdx_blocks.py`
- pytdx tick-by-tick trades: `sync_tdx_tick_trades.py`
- pytdx F10 section tables and text export: `sync_tdx_f10_sections.py`, `export_tdx_f10_txts.py`

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env if needed. CSV storage works by default.

./run_tushare_stock_basic_sync.sh
./run_daily_sync_batches.sh
```

CSV files are written to `./data` by default. For example:

- `data/stock_basic_tushare.csv`
- `data/daily_ohlcv.csv`
- `data/index_daily.csv`
- `data/min5_ohlcv.csv`
- `data/quote_snapshots_unified.csv`

## PostgreSQL

Set this in `.env` to use PostgreSQL:

```env
STORAGE_BACKEND=postgres
MARKET_DB_HOST=localhost
MARKET_DB_PORT=5432
MARKET_DB_NAME=market
MARKET_DB_USER=postgres
MARKET_DB_PASSWORD=
```

Use `STORAGE_BACKEND=both` if you want a local CSV copy and PostgreSQL writes in
the same run. The scripts create or update their own PostgreSQL tables where
needed.

## Credentials

Some providers are optional:

- `TUSHARE_TOKEN` enables Tushare daily/stock_basic data.
- `TICKFLOW_API_KEY` enables TickFlow daily fallback and instruments metadata.
- baostock, Tencent/easyquotation, and pytdx jobs do not require project secrets.

## Cron

Copy `cron.example`, replace `/path/to/daily_stock_data` with your checkout path,
then install the relevant lines with `crontab -e`.

The `run_*.sh` wrappers:

- run from their own repository directory
- read `.env` automatically
- create `logs/` automatically
- allow `PYTHON_BIN=/path/to/python` override

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

## Notes

This project is a data collection utility, not investment advice. Check the
terms and rate limits of each upstream data provider before running scheduled
collection.
