#!/usr/bin/env python3
"""Sync pytdx finance snapshots into PostgreSQL."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import psycopg2
from kline_common import DEFAULT_DB_CONFIG, setup_logging
from psycopg2.extras import Json, execute_values
from storage_common import append_upsert_csv, use_csv, use_postgres
from tdx_common import TdxSymbol, connect_hq_api, load_tdx_stock_universe

LOGGER = setup_logging("./logs/tdx_finance.log")
TABLE_NAME = "stock_finance_tdx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 pytdx 财务快照")
    parser.add_argument("--workers", type=int, default=4, help="并发 worker 数")
    parser.add_argument("--chunk-size", type=int, default=250, help="每个 worker 处理的 symbol 数")
    return parser.parse_args()


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
                    symbol VARCHAR(16) NOT NULL,
                    updated_date DATE NOT NULL,
                    market INTEGER,
                    code VARCHAR(16),
                    province INTEGER,
                    industry INTEGER,
                    ipo_date DATE,
                    liutongguben NUMERIC(24, 4),
                    zongguben NUMERIC(24, 4),
                    gudongrenshu NUMERIC(24, 4),
                    zongzichan NUMERIC(24, 4),
                    jingzichan NUMERIC(24, 4),
                    zhuyingshouru NUMERIC(24, 4),
                    zhuyinglirun NUMERIC(24, 4),
                    jinglirun NUMERIC(24, 4),
                    meigujingzichan NUMERIC(24, 6),
                    raw_payload JSONB NOT NULL,
                    source VARCHAR(32) NOT NULL DEFAULT 'pytdx',
                    fetched_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (symbol, updated_date)
                )
                """
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_symbol ON {TABLE_NAME} (symbol)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_updated_date ON {TABLE_NAME} (updated_date DESC)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_fetched_at ON {TABLE_NAME} (fetched_at DESC)")
        LOGGER.info("✅ pytdx finance 表结构检查完成: %s", TABLE_NAME)
    finally:
        conn.close()


def chunked(items: list[TdxSymbol], size: int) -> list[list[TdxSymbol]]:
    return [items[idx: idx + size] for idx in range(0, len(items), size)]


def parse_compact_date(value) -> dt.date | None:
    if value in (None, "", 0):
        return None
    text = str(int(value))
    if len(text) != 8:
        return None
    return dt.datetime.strptime(text, "%Y%m%d").date()


def normalize_row(symbol: str, payload: dict, fetched_at: dt.datetime) -> tuple | None:
    updated_date = parse_compact_date(payload.get("updated_date"))
    if updated_date is None:
        return None

    normalized = {}
    for key, value in payload.items():
        normalized[key] = value

    return (
        symbol,
        updated_date,
        payload.get("market"),
        payload.get("code"),
        payload.get("province"),
        payload.get("industry"),
        parse_compact_date(payload.get("ipo_date")),
        payload.get("liutongguben"),
        payload.get("zongguben"),
        payload.get("gudongrenshu"),
        payload.get("zongzichan"),
        payload.get("jingzichan"),
        payload.get("zhuyingshouru"),
        payload.get("zhuyinglirun"),
        payload.get("jinglirun"),
        payload.get("meigujingzichan"),
        Json(normalized, dumps=lambda v: json.dumps(v, ensure_ascii=False)),
        "pytdx",
        fetched_at,
        fetched_at,
    )


def fetch_chunk(symbols: list[TdxSymbol], fetched_at: dt.datetime) -> tuple[list[tuple], int]:
    rows: list[tuple] = []
    with connect_hq_api(LOGGER) as api:
        for item in symbols:
            payload = api.get_finance_info(item.market, item.code)
            if not payload:
                continue
            row = normalize_row(item.symbol, payload, fetched_at)
            if row is not None:
                rows.append(row)
    return rows, len(symbols)


def save_rows(rows: list[tuple]) -> int:
    if not rows:
        return 0

    columns = [
        "symbol", "updated_date", "market", "code", "province", "industry", "ipo_date",
        "liutongguben", "zongguben", "gudongrenshu", "zongzichan", "jingzichan",
        "zhuyingshouru", "zhuyinglirun", "jinglirun", "meigujingzichan",
        "raw_payload", "source", "fetched_at", "updated_at",
    ]
    written = 0
    if use_csv():
        csv_rows = []
        for row in rows:
            values = list(row)
            values[16] = json.dumps(values[16].adapted, ensure_ascii=False)
            csv_rows.append(values)
        written = append_upsert_csv(
            pd.DataFrame(csv_rows, columns=columns),
            TABLE_NAME,
            ["symbol", "updated_date"],
            parse_dates=["updated_date"],
        )

    if not use_postgres():
        return written

    sql = f"""
        INSERT INTO {TABLE_NAME} (
            symbol, updated_date, market, code, province, industry, ipo_date,
            liutongguben, zongguben, gudongrenshu, zongzichan, jingzichan,
            zhuyingshouru, zhuyinglirun, jinglirun, meigujingzichan,
            raw_payload, source, fetched_at, updated_at
        )
        VALUES %s
        ON CONFLICT (symbol, updated_date) DO UPDATE SET
            market = EXCLUDED.market,
            code = EXCLUDED.code,
            province = EXCLUDED.province,
            industry = EXCLUDED.industry,
            ipo_date = EXCLUDED.ipo_date,
            liutongguben = EXCLUDED.liutongguben,
            zongguben = EXCLUDED.zongguben,
            gudongrenshu = EXCLUDED.gudongrenshu,
            zongzichan = EXCLUDED.zongzichan,
            jingzichan = EXCLUDED.jingzichan,
            zhuyingshouru = EXCLUDED.zhuyingshouru,
            zhuyinglirun = EXCLUDED.zhuyinglirun,
            jinglirun = EXCLUDED.jinglirun,
            meigujingzichan = EXCLUDED.meigujingzichan,
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


def main() -> None:
    args = parse_args()
    ensure_schema()

    symbols = load_tdx_stock_universe(LOGGER)
    fetched_at = dt.datetime.now(dt.UTC)
    total_rows = 0
    total_symbols = 0

    LOGGER.info("🚀 开始同步 pytdx finance，symbols=%s workers=%s", len(symbols), args.workers)
    batches = chunked(symbols, args.chunk_size)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(fetch_chunk, batch, fetched_at): idx for idx, batch in enumerate(batches, start=1)
        }
        for future in as_completed(futures):
            idx = futures[future]
            rows, symbol_count = future.result()
            written = save_rows(rows)
            total_rows += written
            total_symbols += symbol_count
            LOGGER.info(
                "[batch %s/%s] processed_symbols=%s written_rows=%s total_rows=%s",
                idx,
                len(batches),
                symbol_count,
                written,
                total_rows,
            )

    LOGGER.info("✅ pytdx finance 同步完成，处理股票 %s，写入/更新 %s 条", total_symbols, total_rows)


if __name__ == "__main__":
    main()
