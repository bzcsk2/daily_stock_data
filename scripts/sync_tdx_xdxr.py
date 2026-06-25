#!/usr/bin/env python3
"""Sync pytdx xdxr / share-change events into PostgreSQL."""

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

LOGGER = setup_logging("./logs/tdx_xdxr.log")
TABLE_NAME = "stock_xdxr_tdx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 pytdx 除权除息/股本变化数据")
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
                    event_date DATE NOT NULL,
                    category INTEGER NOT NULL,
                    name VARCHAR(64) NOT NULL,
                    fenhong NUMERIC(24, 6),
                    peigujia NUMERIC(24, 6),
                    songzhuangu NUMERIC(24, 6),
                    peigu NUMERIC(24, 6),
                    suogu NUMERIC(24, 6),
                    panqianliutong NUMERIC(24, 6),
                    panhouliutong NUMERIC(24, 6),
                    qianzongguben NUMERIC(24, 6),
                    houzongguben NUMERIC(24, 6),
                    fenshu NUMERIC(24, 6),
                    xingquanjia NUMERIC(24, 6),
                    raw_payload JSONB NOT NULL,
                    source VARCHAR(32) NOT NULL DEFAULT 'pytdx',
                    fetched_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (symbol, event_date, category, name)
                )
                """
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_symbol ON {TABLE_NAME} (symbol)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_event_date ON {TABLE_NAME} (event_date DESC)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_updated_at ON {TABLE_NAME} (updated_at DESC)")
        LOGGER.info("✅ pytdx xdxr 表结构检查完成: %s", TABLE_NAME)
    finally:
        conn.close()


def chunked(items: list[TdxSymbol], size: int) -> list[list[TdxSymbol]]:
    return [items[idx: idx + size] for idx in range(0, len(items), size)]


def normalize_row(symbol: str, row: dict, fetched_at: dt.datetime) -> tuple | None:
    event_date = dt.date(int(row["year"]), int(row["month"]), int(row["day"]))
    normalized = {}
    for key, value in row.items():
        if pd.isna(value):
            normalized[key] = None
        else:
            normalized[key] = value

    return (
        symbol,
        event_date,
        int(normalized["category"]),
        str(normalized.get("name") or ""),
        normalized.get("fenhong"),
        normalized.get("peigujia"),
        normalized.get("songzhuangu"),
        normalized.get("peigu"),
        normalized.get("suogu"),
        normalized.get("panqianliutong"),
        normalized.get("panhouliutong"),
        normalized.get("qianzongguben"),
        normalized.get("houzongguben"),
        normalized.get("fenshu"),
        normalized.get("xingquanjia"),
        Json(normalized, dumps=lambda v: json.dumps(v, ensure_ascii=False)),
        "pytdx",
        fetched_at,
        fetched_at,
    )


def fetch_chunk(symbols: list[TdxSymbol], fetched_at: dt.datetime) -> tuple[list[tuple], int]:
    rows: list[tuple] = []
    with connect_hq_api(LOGGER) as api:
        for item in symbols:
            df = api.to_df(api.get_xdxr_info(item.market, item.code))
            if df.empty:
                continue
            records = df.where(pd.notna(df), None).to_dict("records")
            for record in records:
                row = normalize_row(item.symbol, record, fetched_at)
                if row is not None:
                    rows.append(row)
    return rows, len(symbols)


def save_rows(rows: list[tuple]) -> int:
    if not rows:
        return 0

    columns = [
        "symbol",
        "event_date",
        "category",
        "name",
        "fenhong",
        "peigujia",
        "songzhuangu",
        "peigu",
        "suogu",
        "panqianliutong",
        "panhouliutong",
        "qianzongguben",
        "houzongguben",
        "fenshu",
        "xingquanjia",
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
            values[15] = json.dumps(values[15].adapted, ensure_ascii=False)
            csv_rows.append(values)
        written = append_upsert_csv(
            pd.DataFrame(csv_rows, columns=columns),
            TABLE_NAME,
            ["symbol", "event_date", "category", "name"],
            parse_dates=["event_date"],
        )

    if not use_postgres():
        return written

    sql = f"""
        INSERT INTO {TABLE_NAME} (
            symbol, event_date, category, name, fenhong, peigujia, songzhuangu,
            peigu, suogu, panqianliutong, panhouliutong, qianzongguben,
            houzongguben, fenshu, xingquanjia, raw_payload, source, fetched_at, updated_at
        )
        VALUES %s
        ON CONFLICT (symbol, event_date, category, name) DO UPDATE SET
            fenhong = EXCLUDED.fenhong,
            peigujia = EXCLUDED.peigujia,
            songzhuangu = EXCLUDED.songzhuangu,
            peigu = EXCLUDED.peigu,
            suogu = EXCLUDED.suogu,
            panqianliutong = EXCLUDED.panqianliutong,
            panhouliutong = EXCLUDED.panhouliutong,
            qianzongguben = EXCLUDED.qianzongguben,
            houzongguben = EXCLUDED.houzongguben,
            fenshu = EXCLUDED.fenshu,
            xingquanjia = EXCLUDED.xingquanjia,
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


def cleanup_stale_rows(symbols: list[str], fetched_at: dt.datetime) -> int:
    if not use_postgres():
        return 0

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM {TABLE_NAME}
                WHERE symbol = ANY(%s)
                  AND fetched_at < %s
                """,
                (symbols, fetched_at),
            )
            return cur.rowcount
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    ensure_schema()

    symbols = load_tdx_stock_universe(LOGGER)
    fetched_at = dt.datetime.now(dt.UTC)
    total_rows = 0
    total_symbols = 0

    LOGGER.info("🚀 开始同步 pytdx xdxr，symbols=%s workers=%s", len(symbols), args.workers)
    batches = chunked(symbols, args.chunk_size)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(fetch_chunk, batch, fetched_at): idx for idx, batch in enumerate(batches, start=1)}
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

    deleted = cleanup_stale_rows([item.symbol for item in symbols], fetched_at)
    LOGGER.info(
        "✅ pytdx xdxr 同步完成，处理股票 %s，写入/更新 %s 条，清理旧记录 %s 条",
        total_symbols,
        total_rows,
        deleted,
    )


if __name__ == "__main__":
    main()
