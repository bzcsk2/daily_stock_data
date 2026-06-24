#!/usr/bin/env python3
"""Shared helpers for unified snapshot storage."""

from __future__ import annotations

import datetime as dt

import psycopg2
from psycopg2 import errors

from kline_common import DEFAULT_DB_CONFIG
from storage_common import use_postgres

UNIFIED_TABLE = "quote_snapshots_unified"


def get_conn():
    if not use_postgres():
        raise RuntimeError("PostgreSQL backend is disabled; set STORAGE_BACKEND=postgres or both to use it")
    return psycopg2.connect(**DEFAULT_DB_CONFIG)


def ensure_unified_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"{UNIFIED_TABLE}_schema",))
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {UNIFIED_TABLE} (
                    id BIGSERIAL,
                    source VARCHAR(16) NOT NULL,
                    source_snapshot_id BIGINT,
                    symbol VARCHAR(20) NOT NULL,
                    name VARCHAR(100),
                    snapshot_time TIMESTAMPTZ NOT NULL,
                    price NUMERIC(15,4),
                    open NUMERIC(15,4),
                    close NUMERIC(15,4),
                    high NUMERIC(15,4),
                    low NUMERIC(15,4),
                    buy NUMERIC(15,4),
                    sell NUMERIC(15,4),
                    amount NUMERIC(20,4),
                    volume_shares NUMERIC(20,4),
                    volume_lots NUMERIC(20,4),
                    change_amount NUMERIC(15,4),
                    change_percent NUMERIC(10,4),
                    amplitude NUMERIC(10,4),
                    turnover_rate NUMERIC(15,4),
                    market_cap NUMERIC(20,2),
                    circulating_cap NUMERIC(20,2),
                    pe NUMERIC(15,4),
                    pb NUMERIC(15,4),
                    limit_up_price NUMERIC(15,4),
                    limit_down_price NUMERIC(15,4),
                    is_limit_up BOOLEAN,
                    is_limit_down BOOLEAN,
                    limit_up_money NUMERIC(20,4),
                    limit_down_money NUMERIC(20,4),
                    limit_up_time TIMESTAMPTZ,
                    volume_ratio NUMERIC(10,4),
                    weicha NUMERIC(20,2),
                    avg_price NUMERIC(15,4),
                    bid1 NUMERIC(15,4),
                    bid1_volume INTEGER,
                    bid2 NUMERIC(15,4),
                    bid2_volume INTEGER,
                    bid3 NUMERIC(15,4),
                    bid3_volume INTEGER,
                    bid4 NUMERIC(15,4),
                    bid4_volume INTEGER,
                    bid5 NUMERIC(15,4),
                    bid5_volume INTEGER,
                    ask1 NUMERIC(15,4),
                    ask1_volume INTEGER,
                    ask2 NUMERIC(15,4),
                    ask2_volume INTEGER,
                    ask3 NUMERIC(15,4),
                    ask3_volume INTEGER,
                    ask4 NUMERIC(15,4),
                    ask4_volume INTEGER,
                    ask5 NUMERIC(15,4),
                    ask5_volume INTEGER,
                    legacy_date VARCHAR(20),
                    legacy_time VARCHAR(20),
                    raw_volume NUMERIC(20,4),
                    raw_turnover NUMERIC(20,4),
                    raw_amount_wan NUMERIC(20,4),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (snapshot_time, source, symbol, id)
                ) PARTITION BY RANGE (snapshot_time)
                """
            )
            cur.execute(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_{UNIFIED_TABLE}_time_source_symbol
                ON {UNIFIED_TABLE} (snapshot_time, source, symbol)
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{UNIFIED_TABLE}_symbol_time
                ON {UNIFIED_TABLE} (symbol, snapshot_time DESC)
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{UNIFIED_TABLE}_source_time
                ON {UNIFIED_TABLE} (source, snapshot_time DESC)
                """
            )
        conn.commit()
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"{UNIFIED_TABLE}_schema",))
        conn.commit()


def create_partition(conn, target_date: dt.date) -> str:
    partition_name = f"{UNIFIED_TABLE}_{target_date.strftime('%Y%m%d')}"
    start = target_date.strftime("%Y-%m-%d 00:00:00")
    end = (target_date + dt.timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = %s
            )
            """,
            (partition_name,),
        )
        exists = cur.fetchone()[0]
        if not exists:
            try:
                cur.execute(
                    f"""
                    CREATE TABLE {partition_name}
                    PARTITION OF {UNIFIED_TABLE}
                    FOR VALUES FROM (%s) TO (%s)
                    """,
                    (start, end),
                )
                conn.commit()
            except errors.DuplicateTable:
                conn.rollback()
    return partition_name
