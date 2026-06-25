#!/usr/bin/env python3
"""Sync grouped pytdx F10 sections into PostgreSQL current tables."""

from __future__ import annotations

import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import psycopg2
from kline_common import DEFAULT_DB_CONFIG, setup_logging
from psycopg2.extras import execute_values
from storage_common import append_upsert_csv, use_csv, use_postgres
from tdx_common import TdxSymbol
from tdx_f10_common import (
    SECTION_GROUPS,
    SECTION_TABLES,
    connect_hq_api,
    extract_update_date,
    fetch_section_texts,
    load_tdx_stock_universe,
    text_hash,
)

LOGGER = setup_logging("./logs/tdx_f10_sections.log")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 pytdx F10 分组章节")
    parser.add_argument("--group", choices=sorted(SECTION_GROUPS), required=True, help="章节分组")
    parser.add_argument("--workers", type=int, default=4, help="并发 worker 数")
    parser.add_argument("--chunk-size", type=int, default=100, help="每个 worker 处理的股票数")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 只股票")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 只股票")
    return parser.parse_args()


def get_conn():
    return psycopg2.connect(**DEFAULT_DB_CONFIG)


def section_table(section_name: str) -> str:
    return SECTION_TABLES[section_name]


def ensure_tables(section_names: list[str]) -> None:
    if not use_postgres():
        return

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            for section_name in section_names:
                table = section_table(section_name)
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        symbol VARCHAR(16) PRIMARY KEY,
                        stock_name VARCHAR(64) NOT NULL,
                        section_name VARCHAR(64) NOT NULL,
                        section_update_date DATE,
                        filename VARCHAR(64) NOT NULL,
                        start_offset INTEGER NOT NULL,
                        section_length INTEGER NOT NULL,
                        raw_length INTEGER NOT NULL,
                        content_hash VARCHAR(64) NOT NULL,
                        content_text TEXT NOT NULL,
                        source VARCHAR(32) NOT NULL DEFAULT 'pytdx',
                        fetched_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_section_update_date ON {table} (section_update_date DESC)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_updated_at ON {table} (updated_at DESC)")
        LOGGER.info("✅ F10 分组表结构检查完成: %s", ", ".join(section_table(name) for name in section_names))
    finally:
        conn.close()


def chunked(items: list[TdxSymbol], size: int) -> list[list[TdxSymbol]]:
    return [items[idx: idx + size] for idx in range(0, len(items), size)]


def fetch_chunk(symbols: list[TdxSymbol], section_names: list[str], fetched_at: dt.datetime) -> dict[str, list[tuple]]:
    rows_by_table = {section_table(name): [] for name in section_names}
    with connect_hq_api(LOGGER) as api:
        for symbol in symbols:
            payload = fetch_section_texts(api, symbol, section_names)
            for section_name, (meta, content) in payload.items():
                table = section_table(section_name)
                rows_by_table[table].append(
                    (
                        meta.symbol,
                        meta.stock_name,
                        meta.section_name,
                        extract_update_date(content),
                        meta.filename,
                        meta.start_offset,
                        meta.section_length,
                        len(content),
                        text_hash(content),
                        content,
                        "pytdx",
                        fetched_at,
                        fetched_at,
                    )
                )
    return rows_by_table


def save_rows(table: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    columns = [
        "symbol", "stock_name", "section_name", "section_update_date", "filename",
        "start_offset", "section_length", "raw_length", "content_hash", "content_text",
        "source", "fetched_at", "updated_at",
    ]
    written = 0
    if use_csv():
        written = append_upsert_csv(
            pd.DataFrame(rows, columns=columns),
            table,
            ["symbol"],
            parse_dates=["section_update_date"],
        )

    if not use_postgres():
        return written

    sql = f"""
        INSERT INTO {table} (
            symbol, stock_name, section_name, section_update_date, filename,
            start_offset, section_length, raw_length, content_hash, content_text,
            source, fetched_at, updated_at
        )
        VALUES %s
        ON CONFLICT (symbol) DO UPDATE SET
            stock_name = EXCLUDED.stock_name,
            section_name = EXCLUDED.section_name,
            section_update_date = EXCLUDED.section_update_date,
            filename = EXCLUDED.filename,
            start_offset = EXCLUDED.start_offset,
            section_length = EXCLUDED.section_length,
            raw_length = EXCLUDED.raw_length,
            content_hash = EXCLUDED.content_hash,
            content_text = EXCLUDED.content_text,
            source = EXCLUDED.source,
            fetched_at = EXCLUDED.fetched_at,
            updated_at = EXCLUDED.updated_at
    """
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=500)
        return len(rows)
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    section_names = SECTION_GROUPS[args.group]
    ensure_tables(section_names)

    symbols = load_tdx_stock_universe(LOGGER)
    if args.offset:
        symbols = symbols[args.offset:]
    if args.limit is not None:
        symbols = symbols[:args.limit]
    fetched_at = dt.datetime.now(dt.UTC)
    totals = {section_table(name): 0 for name in section_names}

    LOGGER.info(
        "🚀 开始同步 F10 章节，group=%s sections=%s symbols=%s workers=%s",
        args.group,
        ",".join(section_names),
        len(symbols),
        args.workers,
    )

    batches = chunked(symbols, args.chunk_size)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(fetch_chunk, batch, section_names, fetched_at): idx for idx, batch in enumerate(batches, start=1)
        }
        for future in as_completed(futures):
            idx = futures[future]
            rows_by_table = future.result()
            batch_written = 0
            for table, rows in rows_by_table.items():
                written = save_rows(table, rows)
                totals[table] += written
                batch_written += written
            LOGGER.info(
                "[batch %s/%s] written_rows=%s totals=%s",
                idx,
                len(batches),
                batch_written,
                {k: v for k, v in totals.items()},
            )

    LOGGER.info("✅ F10 章节同步完成，group=%s totals=%s", args.group, totals)


if __name__ == "__main__":
    main()
