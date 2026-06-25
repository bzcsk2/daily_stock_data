#!/usr/bin/env python3
"""Refresh Tushare stock_basic into a local reference table.

This is a supplement data source alongside the baostock K-line sync.
Unlike daily/min5 jobs, stock_basic is a full-table refresh of current
reference metadata rather than per-symbol incremental bars.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
from collections.abc import Iterable

import pandas as pd
import psycopg2
import tushare as ts
from kline_common import DEFAULT_DB_CONFIG, latest_trade_date, load_symbols, setup_logging
from psycopg2.extras import execute_values
from storage_common import use_csv, use_postgres, write_csv_table

LOGGER = setup_logging("./logs/tushare_stock_basic.log")

TABLE_NAME = "stock_basic_tushare"
FIELDS = [
    "ts_code",
    "symbol",
    "name",
    "area",
    "industry",
    "fullname",
    "enname",
    "cnspell",
    "market",
    "exchange",
    "curr_type",
    "list_status",
    "list_date",
    "delist_date",
    "is_hs",
    "act_name",
    "act_ent_type",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 Tushare stock_basic 基础资料")
    parser.add_argument("--list-status", default="L", help="股票状态，默认 L（上市）")
    return parser.parse_args()


def clear_proxy_env() -> None:
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(key, None)
    os.environ.setdefault("NO_PROXY", "*")


def get_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 TUSHARE_TOKEN，无法请求 Tushare")
    return token


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
                    ts_code VARCHAR(16) PRIMARY KEY,
                    symbol VARCHAR(16) NOT NULL,
                    name VARCHAR(64) NOT NULL,
                    area VARCHAR(64),
                    industry VARCHAR(128),
                    fullname TEXT,
                    enname TEXT,
                    cnspell VARCHAR(64),
                    market VARCHAR(32),
                    exchange VARCHAR(16),
                    curr_type VARCHAR(16),
                    list_status VARCHAR(4) NOT NULL,
                    list_date DATE,
                    delist_date DATE,
                    is_hs VARCHAR(8),
                    act_name VARCHAR(128),
                    act_ent_type VARCHAR(64),
                    source VARCHAR(32) NOT NULL DEFAULT 'tushare',
                    fetched_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_symbol ON {TABLE_NAME} (symbol)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_list_status ON {TABLE_NAME} (list_status)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_updated_at ON {TABLE_NAME} (updated_at DESC)"
            )
        LOGGER.info("✅ 基础资料表结构检查完成: %s", TABLE_NAME)
    finally:
        conn.close()


def init_tushare() -> ts.pro_api:
    clear_proxy_env()
    ts.set_token(get_token())
    return ts.pro_api()


def fetch_stock_basic(pro, list_status: str) -> pd.DataFrame:
    df = pro.stock_basic(list_status=list_status, fields=",".join(FIELDS))
    if df.empty:
        LOGGER.warning("⚠ stock_basic 返回空结果，list_status=%s", list_status)
        return df

    for column in ("list_date", "delist_date"):
        df[column] = pd.to_datetime(df[column], format="%Y%m%d", errors="coerce").dt.date
    return df


def save_df(df: pd.DataFrame, fetched_at: dt.datetime) -> int:
    if df.empty:
        return 0

    output_df = df.reindex(columns=FIELDS).copy()
    output_df["source"] = "tushare"
    output_df["fetched_at"] = fetched_at
    output_df["updated_at"] = fetched_at

    written = 0
    if use_csv():
        written = write_csv_table(output_df, TABLE_NAME)

    if not use_postgres():
        return written

    rows = [tuple(row) for row in output_df.itertuples(index=False, name=None)]

    sql = f"""
        INSERT INTO {TABLE_NAME} (
            ts_code, symbol, name, area, industry, fullname, enname, cnspell,
            market, exchange, curr_type, list_status, list_date, delist_date,
            is_hs, act_name, act_ent_type, source, fetched_at, updated_at
        )
        VALUES %s
        ON CONFLICT (ts_code) DO UPDATE SET
            symbol = EXCLUDED.symbol,
            name = EXCLUDED.name,
            area = EXCLUDED.area,
            industry = EXCLUDED.industry,
            fullname = EXCLUDED.fullname,
            enname = EXCLUDED.enname,
            cnspell = EXCLUDED.cnspell,
            market = EXCLUDED.market,
            exchange = EXCLUDED.exchange,
            curr_type = EXCLUDED.curr_type,
            list_status = EXCLUDED.list_status,
            list_date = EXCLUDED.list_date,
            delist_date = EXCLUDED.delist_date,
            is_hs = EXCLUDED.is_hs,
            act_name = EXCLUDED.act_name,
            act_ent_type = EXCLUDED.act_ent_type,
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


def cleanup_stale_rows(list_status: str, fetched_at: dt.datetime) -> int:
    if not use_postgres():
        return 0

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM {TABLE_NAME}
                WHERE list_status = %s
                  AND fetched_at < %s
                """,
                (list_status, fetched_at),
            )
            deleted = cur.rowcount
        return deleted
    finally:
        conn.close()


def to_tushare_code(baostock_code: str) -> str | None:
    code = baostock_code.lower()
    if code.startswith("sh."):
        return code[3:].upper() + ".SH"
    if code.startswith("sz."):
        return code[3:].upper() + ".SZ"
    return None


def sample_values(items: Iterable[str], limit: int = 10) -> list[str]:
    return sorted(list(items))[:limit]


def compare_with_baostock(df: pd.DataFrame) -> None:
    logger = logging.getLogger("tushare_stock_basic_compare")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False

    as_of_date = latest_trade_date().isoformat()
    baostock_symbols = load_symbols(logger, as_of_date=as_of_date, include_indices=False)
    bs_codes = {code for code in (to_tushare_code(item.db_symbol) for item in baostock_symbols) if code}
    ts_codes = set(df["ts_code"])

    only_bs = bs_codes - ts_codes
    only_ts = ts_codes - bs_codes
    LOGGER.info(
        "📊 对比现有 baostock 股票清单: baostock=%s tushare=%s 交集=%s 仅baostock=%s 仅tushare=%s",
        len(bs_codes),
        len(ts_codes),
        len(bs_codes & ts_codes),
        len(only_bs),
        len(only_ts),
    )
    if only_bs:
        LOGGER.info("样例: 仅 baostock 存在 %s", sample_values(only_bs))
    if only_ts:
        LOGGER.info("样例: 仅 tushare 存在 %s", sample_values(only_ts))


def main() -> None:
    args = parse_args()
    ensure_schema()
    pro = init_tushare()

    LOGGER.info("🚀 开始同步 Tushare stock_basic，list_status=%s", args.list_status)
    fetched_at = dt.datetime.now(dt.timezone.utc)
    df = fetch_stock_basic(pro, args.list_status)
    inserted = save_df(df, fetched_at)
    deleted = cleanup_stale_rows(args.list_status, fetched_at)

    LOGGER.info("✅ Tushare stock_basic 同步完成，写入/更新 %s 条，清理旧记录 %s 条", inserted, deleted)
    compare_with_baostock(df)


if __name__ == "__main__":
    main()
