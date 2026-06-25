#!/usr/bin/env python3
"""Refresh TickFlow instrument metadata into a local reference table."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections.abc import Iterable

import pandas as pd
import psycopg2
import requests
from kline_common import DEFAULT_DB_CONFIG, latest_trade_date, load_symbols, setup_logging
from psycopg2.extras import Json, execute_values
from storage_common import read_csv_table, use_csv, use_postgres, write_csv_table

LOGGER = setup_logging("./logs/tickflow_instruments.log")

TABLE_NAME = "stock_basic_tickflow"
BASE_URL = os.environ.get("TICKFLOW_BASE_URL", "https://api.tickflow.org")
DEFAULT_BATCH_SIZE = 200


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 TickFlow instruments 基础资料")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="单次请求的 symbols 数量")
    return parser.parse_args()


def get_api_key() -> str:
    api_key = os.environ.get("TICKFLOW_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("缺少 TICKFLOW_API_KEY，无法请求 TickFlow")
    return api_key


def get_conn():
    return psycopg2.connect(**DEFAULT_DB_CONFIG)


def ensure_schema() -> None:
    if not use_postgres():
        return

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    symbol VARCHAR(16) PRIMARY KEY,
                    exchange VARCHAR(16),
                    code VARCHAR(16),
                    name VARCHAR(64),
                    region VARCHAR(16),
                    instrument_type VARCHAR(32),
                    ext_type VARCHAR(32),
                    listing_date DATE,
                    total_shares NUMERIC(24, 4),
                    float_shares NUMERIC(24, 4),
                    tick_size NUMERIC(18, 6),
                    limit_up NUMERIC(18, 6),
                    limit_down NUMERIC(18, 6),
                    raw_payload JSONB NOT NULL,
                    source VARCHAR(32) NOT NULL DEFAULT 'tickflow',
                    fetched_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_exchange ON {TABLE_NAME} (exchange)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_code ON {TABLE_NAME} (code)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_updated_at ON {TABLE_NAME} (updated_at DESC)"
            )
        LOGGER.info("✅ TickFlow 基础资料表结构检查完成: %s", TABLE_NAME)
    finally:
        conn.close()


def fetch_symbols_from_stock_basic_tushare() -> list[str]:
    df = read_csv_table("stock_basic_tushare")
    if not df.empty and "ts_code" in df.columns:
        if "list_status" in df.columns:
            df = df[df["list_status"] == "L"]
        return sorted(set(str(item) for item in df["ts_code"].dropna()))

    if not use_postgres():
        return []

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ts_code
                FROM stock_basic_tushare
                WHERE list_status = 'L'
                ORDER BY ts_code
                """
            )
            return [row[0] for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def to_tickflow_symbol(baostock_code: str) -> str | None:
    code = baostock_code.lower()
    if code.startswith("sh."):
        return code[3:].upper() + ".SH"
    if code.startswith("sz."):
        return code[3:].upper() + ".SZ"
    return None


def fetch_symbols_from_baostock() -> list[str]:
    symbols = load_symbols(LOGGER, as_of_date=latest_trade_date().isoformat(), include_indices=False)
    result = []
    for item in symbols:
        tickflow_symbol = to_tickflow_symbol(item.db_symbol)
        if tickflow_symbol:
            result.append(tickflow_symbol)
    return sorted(set(result))


def fetch_symbols_from_daily_ohlcv() -> list[str]:
    df = read_csv_table("daily_ohlcv")
    if not df.empty and "symbol" in df.columns:
        symbols = df["symbol"].dropna().astype(str)
        return sorted(set(item for item in symbols if item.endswith((".SH", ".SZ"))))

    if not use_postgres():
        return []

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT symbol
                FROM daily_ohlcv
                WHERE symbol ~ '^[0-9]{6}\.(SH|SZ)$'
                ORDER BY symbol
                """
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def load_symbol_universe() -> tuple[str, list[str]]:
    ts_symbols = fetch_symbols_from_stock_basic_tushare()
    if ts_symbols:
        return "stock_basic_tushare", ts_symbols

    try:
        bs_symbols = fetch_symbols_from_baostock()
        if bs_symbols:
            return "baostock_runtime", bs_symbols
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("⚠ 从 baostock 获取股票清单失败，回退本地日线表: %s", exc)

    daily_symbols = fetch_symbols_from_daily_ohlcv()
    if daily_symbols:
        return "daily_ohlcv_distinct", daily_symbols

    raise RuntimeError("无法构建 TickFlow instruments 的股票清单")


def chunked(items: list[str], size: int) -> Iterable[list[str]]:
    for idx in range(0, len(items), size):
        yield items[idx: idx + size]


def fetch_instruments_batch(symbols: list[str]) -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/v1/instruments",
        params={"symbols": ",".join(symbols)},
        headers={"x-api-key": get_api_key(), "accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", [])


def normalize_record(record: dict, fetched_at: dt.datetime) -> tuple:
    ext = record.get("ext") or {}
    listing_date = ext.get("listing_date")
    if listing_date:
        listing_date = dt.date.fromisoformat(listing_date)

    return (
        record.get("symbol"),
        record.get("exchange"),
        record.get("code"),
        record.get("name"),
        record.get("region"),
        record.get("type"),
        ext.get("type"),
        listing_date,
        ext.get("total_shares"),
        ext.get("float_shares"),
        ext.get("tick_size"),
        ext.get("limit_up"),
        ext.get("limit_down"),
        Json(record, dumps=lambda v: json.dumps(v, ensure_ascii=False)),
        "tickflow",
        fetched_at,
        fetched_at,
    )


def save_rows(rows: list[tuple]) -> int:
    if not rows:
        return 0

    columns = [
        "symbol",
        "exchange",
        "code",
        "name",
        "region",
        "instrument_type",
        "ext_type",
        "listing_date",
        "total_shares",
        "float_shares",
        "tick_size",
        "limit_up",
        "limit_down",
        "raw_payload",
        "source",
        "fetched_at",
        "updated_at",
    ]
    written = 0
    if use_csv():
        csv_rows = []
        for row in rows:
            values = list(row)
            values[13] = json.dumps(values[13].adapted, ensure_ascii=False)
            csv_rows.append(values)
        written = write_csv_table(pd.DataFrame(csv_rows, columns=columns), TABLE_NAME)

    if not use_postgres():
        return written

    sql = f"""
        INSERT INTO {TABLE_NAME} (
            symbol, exchange, code, name, region, instrument_type, ext_type,
            listing_date, total_shares, float_shares, tick_size, limit_up, limit_down,
            raw_payload, source, fetched_at, updated_at
        )
        VALUES %s
        ON CONFLICT (symbol) DO UPDATE SET
            exchange = EXCLUDED.exchange,
            code = EXCLUDED.code,
            name = EXCLUDED.name,
            region = EXCLUDED.region,
            instrument_type = EXCLUDED.instrument_type,
            ext_type = EXCLUDED.ext_type,
            listing_date = EXCLUDED.listing_date,
            total_shares = EXCLUDED.total_shares,
            float_shares = EXCLUDED.float_shares,
            tick_size = EXCLUDED.tick_size,
            limit_up = EXCLUDED.limit_up,
            limit_down = EXCLUDED.limit_down,
            raw_payload = EXCLUDED.raw_payload,
            source = EXCLUDED.source,
            fetched_at = EXCLUDED.fetched_at,
            updated_at = EXCLUDED.updated_at
    """

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=1000)
        return len(rows)
    finally:
        conn.close()


def cleanup_stale_rows(fetched_at: dt.datetime) -> int:
    if not use_postgres():
        return 0

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM {TABLE_NAME}
                WHERE fetched_at < %s
                """,
                (fetched_at,),
            )
            return cur.rowcount
    finally:
        conn.close()


def sample_values(items: Iterable[str], limit: int = 10) -> list[str]:
    return sorted(set(items))[:limit]


def main() -> None:
    args = parse_args()
    ensure_schema()

    source_name, symbols = load_symbol_universe()
    LOGGER.info("🚀 开始同步 TickFlow instruments，symbol_source=%s，总数=%s", source_name, len(symbols))

    fetched_at = dt.datetime.now(dt.timezone.utc)
    total_written = 0
    requested: set[str] = set()
    returned: set[str] = set()

    for index, batch in enumerate(chunked(symbols, args.batch_size), start=1):
        requested.update(batch)
        data = fetch_instruments_batch(batch)
        rows = [normalize_record(item, fetched_at) for item in data if item.get("symbol")]
        total_written += save_rows(rows)
        returned.update(item.get("symbol") for item in data if item.get("symbol"))
        LOGGER.info(
            "[%s] batch_size=%s returned=%s total_written=%s",
            index,
            len(batch),
            len(rows),
            total_written,
        )

    deleted = cleanup_stale_rows(fetched_at)
    missing = requested - returned
    LOGGER.info(
        "✅ TickFlow instruments 同步完成，写入/更新 %s 条，清理旧记录 %s 条，未返回 %s 条",
        total_written,
        deleted,
        len(missing),
    )
    if missing:
        LOGGER.warning("未返回样例: %s", sample_values(missing))


if __name__ == "__main__":
    main()
