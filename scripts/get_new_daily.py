#!/usr/bin/env python3
"""Incremental daily K-line sync."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress

import baostock as bs
import pandas as pd
import requests
import tushare as ts
from psycopg2.extras import execute_values
from psycopg2.pool import ThreadedConnectionPool

from kline_common import (
    DEFAULT_DB_CONFIG,
    SymbolInfo,
    baostock_to_ts_code,
    fetch_trade_dates,
    latest_trade_date,
    load_env_file,
    load_symbols,
    setup_logging,
)
from storage_common import append_upsert_csv, read_csv_table, use_csv, use_postgres

LOGGER = setup_logging("./logs/get_new_daily.log")

START_DATE = "2019-01-01"
STOCK_TABLE = "daily_ohlcv"
INDEX_TABLE = "index_daily"
DEFAULT_WORKERS = 1
DEFAULT_BATCH_SIZE = 2000
TICKFLOW_BASE_URL = os.environ.get("TICKFLOW_BASE_URL", "https://api.tickflow.org")

STOCK_FIELDS = [
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "turn",
    "peTTM",
    "pbMRQ",
    "psTTM",
    "pcfNcfTTM",
    "isST",
]
INDEX_FIELDS = ["date", "code", "open", "high", "low", "close", "volume", "amount"]

pool: ThreadedConnectionPool | None = None
_tushare_pro = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="增量同步日线 K 线")
    parser.add_argument("--start-date", default=START_DATE, help="初始起始日期，默认 2019-01-01")
    parser.add_argument("--end-date", default=None, help="结束日期，默认最近交易日")
    parser.add_argument("--repair-days", type=int, default=5, help="额外回补最近 N 个交易日")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_WORKERS, help="并发线程数")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个标的，用于测试")
    parser.add_argument("--offset", type=int, default=0, help="从第 N 个标的开始处理")
    return parser.parse_args()


def init_pool(max_workers: int) -> None:
    global pool
    if not use_postgres():
        return
    pool = ThreadedConnectionPool(2, max_workers + 4, **DEFAULT_DB_CONFIG)
    LOGGER.info("✅ 数据库连接池初始化完成")


def init_external_providers() -> None:
    global _tushare_pro
    load_env_file(os.environ.get("TUSHARE_ENV_FILE", ".env"))
    load_env_file(os.environ.get("TICKFLOW_ENV_FILE", ".env"))

    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        ts.set_token(token)
        _tushare_pro = ts.pro_api()
        LOGGER.info("✅ 已启用 Tushare 日线补充源")
    else:
        _tushare_pro = None
        LOGGER.warning("⚠ 未配置 TUSHARE_TOKEN，股票日线将跳过 Tushare")

    if os.environ.get("TICKFLOW_API_KEY", "").strip():
        LOGGER.info("✅ 已启用 TickFlow 日线补充源")
    else:
        LOGGER.warning("⚠ 未配置 TICKFLOW_API_KEY，股票日线将跳过 TickFlow")


def get_conn():
    if pool is None:
        raise RuntimeError("数据库连接池未初始化")
    return pool.getconn()


def put_conn(conn) -> None:
    if pool is not None:
        pool.putconn(conn)


def ensure_unique_time_symbol(table: str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT regexp_replace(indexdef, '[" ]', '', 'g')
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = %s
                """,
                (table,),
            )
            definitions = [row[0].upper() for row in cur.fetchall()]
            if any("UNIQUEINDEX" in definition and "(TIME,SYMBOL)" in definition for definition in definitions):
                return

            try:
                cur.execute(f"CREATE UNIQUE INDEX uk_{table}_time_symbol ON {table} (time, symbol)")
            except Exception as exc:
                conn.rollback()
                LOGGER.warning("⚠ %s 存在重复键，先自动去重再补唯一索引: %s", table, exc)
                with conn.cursor() as dedupe_cur:
                    dedupe_cur.execute(
                        f"""
                        DELETE FROM {table}
                        WHERE ctid IN (
                            SELECT ctid
                            FROM (
                                SELECT ctid,
                                       ROW_NUMBER() OVER (
                                           PARTITION BY time, symbol
                                           ORDER BY ctid DESC
                                       ) AS rn
                                FROM {table}
                            ) dup
                            WHERE rn > 1
                        )
                        """
                    )
                    deduped = dedupe_cur.rowcount
                    LOGGER.info("🧹 %s 去重删除 %s 条重复行", table, deduped)
                conn.commit()
                with conn.cursor() as index_cur:
                    index_cur.execute(f"CREATE UNIQUE INDEX uk_{table}_time_symbol ON {table} (time, symbol)")
        conn.commit()
    finally:
        put_conn(conn)


def ensure_schema() -> None:
    if not use_postgres():
        return

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {STOCK_TABLE} (
                    time TIMESTAMPTZ NOT NULL,
                    symbol VARCHAR(20) NOT NULL,
                    open NUMERIC(20,4) NOT NULL,
                    high NUMERIC(20,4) NOT NULL,
                    low NUMERIC(20,4) NOT NULL,
                    close NUMERIC(20,4) NOT NULL,
                    volume BIGINT NOT NULL,
                    turnover NUMERIC(20,2) NOT NULL,
                    turn NUMERIC(10,6),
                    pe_ttm NUMERIC(20,4),
                    pb_mrq NUMERIC(20,4),
                    ps_ttm NUMERIC(20,4),
                    pcf_ncf_ttm NUMERIC(20,4),
                    is_st BOOLEAN DEFAULT FALSE
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {INDEX_TABLE} (
                    time TIMESTAMPTZ NOT NULL,
                    symbol VARCHAR(20) NOT NULL,
                    open NUMERIC(20,4) NOT NULL,
                    high NUMERIC(20,4) NOT NULL,
                    low NUMERIC(20,4) NOT NULL,
                    close NUMERIC(20,4) NOT NULL,
                    volume BIGINT NOT NULL,
                    turnover NUMERIC(20,2) NOT NULL
                )
                """
            )
        conn.commit()
        ensure_unique_time_symbol(STOCK_TABLE)
        ensure_unique_time_symbol(INDEX_TABLE)
        LOGGER.info("✅ 日线表结构检查完成")
    finally:
        put_conn(conn)


def get_existing_dates(symbol: SymbolInfo, start_date: dt.date, end_date: dt.date) -> set[dt.date]:
    table = INDEX_TABLE if symbol.is_index else STOCK_TABLE
    if use_csv():
        df = read_csv_table(table, parse_dates=["time"])
        if df.empty or "symbol" not in df.columns or "time" not in df.columns:
            return set()
        rows = df[df["symbol"] == symbol.db_symbol].copy()
        if rows.empty:
            return set()
        times = pd.to_datetime(rows["time"], errors="coerce")
        mask = (times >= pd.Timestamp(start_date)) & (times < pd.Timestamp(end_date + dt.timedelta(days=1)))
        return set(times[mask].dt.date.dropna())

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT time::date
                FROM {table}
                WHERE symbol = %s
                  AND time >= %s
                  AND time < %s
                """,
                (symbol.db_symbol, start_date, end_date + dt.timedelta(days=1)),
            )
            return {row[0] for row in cur.fetchall()}
    finally:
        put_conn(conn)


def get_sync_ranges(symbol: SymbolInfo, trade_dates: list[dt.date], repair_days: int) -> list[tuple[dt.date, dt.date]]:
    if not trade_dates:
        return []

    table = INDEX_TABLE if symbol.is_index else STOCK_TABLE
    if use_csv():
        df = read_csv_table(table, parse_dates=["time"])
        if df.empty or "symbol" not in df.columns or "time" not in df.columns:
            last_date = None
        else:
            rows = df[df["symbol"] == symbol.db_symbol]
            times = pd.to_datetime(rows["time"], errors="coerce")
            last_date = times.max().date() if not rows.empty and not pd.isna(times.max()) else None
    else:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT MAX(time)::date FROM {table} WHERE symbol = %s", (symbol.db_symbol,))
                last_date = cur.fetchone()[0]
        finally:
            put_conn(conn)

    if last_date is None:
        return [(trade_dates[0], trade_dates[-1])]

    recent_dates = trade_dates[max(0, len(trade_dates) - repair_days) :]
    missing_recent = set(recent_dates) - get_existing_dates(symbol, recent_dates[0], recent_dates[-1])

    pending = [date for date in trade_dates if date > last_date]
    pending.extend(date for date in recent_dates if date in missing_recent and date <= last_date)
    pending = sorted(set(pending))

    if not pending:
        return []

    return [(pending[0], pending[-1])]


def _bs_login() -> None:
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")


def _normalize_stock_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    optional_cols = ["turn", "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm", "is_st"]
    for column in optional_cols:
        if column not in df.columns:
            df[column] = None
    if "is_st" in df.columns:
        df["is_st"] = df["is_st"].astype("boolean").fillna(False).astype(bool)

    required = ["time", "symbol", "open", "high", "low", "close", "volume", "turnover"]
    return df[required + optional_cols]


def download_daily_baostock(symbol: SymbolInfo, start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    fields = STOCK_FIELDS if not symbol.is_index else INDEX_FIELDS
    rs = bs.query_history_k_data_plus(
        symbol.baostock_code,
        ",".join(fields),
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        frequency="d",
        adjustflag="3",
    )
    if rs.error_code != "0":
        raise RuntimeError(f"{symbol.baostock_code} 下载失败: {rs.error_msg}")

    rows: list[list[str]] = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=rs.fields)
    numeric_columns = [col for col in df.columns if col not in {"date", "code", "isST"}]
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
    if "isST" in df.columns:
        df["isST"] = df["isST"].map({"1": True, "0": False}).fillna(False)

    df = df.dropna(subset=["date", "open", "high", "low", "close", "volume", "amount"])
    df = df[(df[["open", "high", "low", "close", "volume", "amount"]] != 0).all(axis=1)]

    rename_map = {
        "date": "time",
        "code": "symbol",
        "amount": "turnover",
        "peTTM": "pe_ttm",
        "pbMRQ": "pb_mrq",
        "psTTM": "ps_ttm",
        "pcfNcfTTM": "pcf_ncf_ttm",
        "isST": "is_st",
    }
    df = df.rename(columns=rename_map)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    df = df.dropna(subset=["time"])
    if symbol.is_index:
        return df
    return _normalize_stock_df(df)


def download_daily_tushare(symbol: SymbolInfo, start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    if _tushare_pro is None:
        raise RuntimeError("Tushare 未配置")
    ts_code = baostock_to_ts_code(symbol.baostock_code)
    if not ts_code:
        raise RuntimeError(f"无法转换股票代码: {symbol.baostock_code}")

    df = _tushare_pro.daily(ts_code=ts_code, start_date=start_date.strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"))
    if df.empty:
        return pd.DataFrame()

    df = df.rename(
        columns={
            "trade_date": "time",
            "vol": "volume",
            "amount": "turnover",
        }
    )
    df["time"] = pd.to_datetime(df["time"], format="%Y%m%d", errors="coerce")
    df["symbol"] = symbol.db_symbol
    df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce") * 1000
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    for column in ("open", "high", "low", "close"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["time", "open", "high", "low", "close", "volume", "turnover"])
    return _normalize_stock_df(df)


def download_daily_tickflow(symbol: SymbolInfo, start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    api_key = os.environ.get("TICKFLOW_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TickFlow 未配置")
    ts_code = baostock_to_ts_code(symbol.baostock_code)
    if not ts_code:
        raise RuntimeError(f"无法转换股票代码: {symbol.baostock_code}")

    start_time = int(dt.datetime.combine(start_date, dt.time.min).timestamp() * 1000)
    end_time = int(dt.datetime.combine(end_date, dt.time.max).timestamp() * 1000)
    response = requests.get(
        f"{TICKFLOW_BASE_URL}/v1/klines",
        params={
            "symbol": ts_code,
            "period": "1d",
            "start_time": start_time,
            "end_time": end_time,
        },
        headers={"x-api-key": api_key, "accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json().get("data", {})
    if not payload or not payload.get("timestamp"):
        return pd.DataFrame()

    df = pd.DataFrame(payload)
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    df["symbol"] = symbol.db_symbol
    for column in ("open", "high", "low", "close", "volume", "amount"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.rename(columns={"amount": "turnover"})
    df["volume"] = df["volume"].fillna(0).astype("int64")
    df = df.dropna(subset=["time", "open", "high", "low", "close", "volume", "turnover"])
    return _normalize_stock_df(df)


def download_daily(symbol: SymbolInfo, start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    if symbol.is_index:
        return download_daily_baostock(symbol, start_date, end_date)

    providers = [
        ("tushare", download_daily_tushare),
        ("tickflow", download_daily_tickflow),
        ("baostock", download_daily_baostock),
    ]
    last_error: Exception | None = None
    for provider_name, provider in providers:
        try:
            df = provider(symbol, start_date, end_date)
            if not df.empty:
                LOGGER.info("ℹ %s %s~%s 使用 %s 获取 %s 行", symbol.baostock_code, start_date, end_date, provider_name, len(df))
            return df
        except Exception as exc:
            last_error = exc
            LOGGER.warning("⚠ %s %s~%s %s 失败: %s", symbol.baostock_code, start_date, end_date, provider_name, exc)
    if last_error is not None:
        raise last_error
    return pd.DataFrame()


def save_df(df: pd.DataFrame, table: str) -> int:
    if df.empty:
        return 0

    if table == STOCK_TABLE:
        columns = [
            "time",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "turn",
            "pe_ttm",
            "pb_mrq",
            "ps_ttm",
            "pcf_ncf_ttm",
            "is_st",
        ]
        sql = f"""
            INSERT INTO {table}
            (time, symbol, open, high, low, close, volume, turnover, turn, pe_ttm, pb_mrq, ps_ttm, pcf_ncf_ttm, is_st)
            VALUES %s
            ON CONFLICT (time, symbol) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                turnover = EXCLUDED.turnover,
                turn = COALESCE(EXCLUDED.turn, {table}.turn),
                pe_ttm = COALESCE(EXCLUDED.pe_ttm, {table}.pe_ttm),
                pb_mrq = COALESCE(EXCLUDED.pb_mrq, {table}.pb_mrq),
                ps_ttm = COALESCE(EXCLUDED.ps_ttm, {table}.ps_ttm),
                pcf_ncf_ttm = COALESCE(EXCLUDED.pcf_ncf_ttm, {table}.pcf_ncf_ttm),
                is_st = COALESCE(EXCLUDED.is_st, {table}.is_st)
        """
    else:
        columns = ["time", "symbol", "open", "high", "low", "close", "volume", "turnover"]
        sql = f"""
            INSERT INTO {table}
            (time, symbol, open, high, low, close, volume, turnover)
            VALUES %s
            ON CONFLICT (time, symbol) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                turnover = EXCLUDED.turnover
        """

    output_df = df.reindex(columns=columns)
    written = 0
    if use_csv():
        written = append_upsert_csv(output_df, table, ["time", "symbol"], parse_dates=["time"])

    if use_postgres():
        rows = [tuple(row) for row in output_df.itertuples(index=False, name=None)]
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                execute_values(cur, sql, rows, page_size=DEFAULT_BATCH_SIZE)
            conn.commit()
            written = len(rows)
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)

    return written


def process_symbol(symbol: SymbolInfo, trade_dates: list[dt.date], repair_days: int) -> tuple[str, int]:
    ranges = get_sync_ranges(symbol, trade_dates, repair_days)
    if not ranges:
        return symbol.baostock_code, 0

    inserted = 0
    table = INDEX_TABLE if symbol.is_index else STOCK_TABLE
    for start_date, end_date in ranges:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                _bs_login()
                try:
                    df = download_daily(symbol, start_date, end_date)
                    inserted += save_df(df, table)
                    time.sleep(0.15)
                    last_error = None
                    break
                finally:
                    with suppress(Exception):
                        bs.logout()
            except Exception as exc:
                last_error = exc
                LOGGER.warning(
                    "⚠ %s %s~%s 第%s次失败: %s",
                    symbol.baostock_code,
                    start_date,
                    end_date,
                    attempt + 1,
                    exc,
                )
                time.sleep(2 ** attempt)
        if last_error is not None:
            raise last_error
    return symbol.baostock_code, inserted


def main() -> None:
    args = parse_args()
    latest_date = dt.datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else latest_trade_date()
    trade_dates = fetch_trade_dates(args.start_date, latest_date.isoformat())
    if not trade_dates:
        raise RuntimeError("交易日列表为空，终止同步")

    init_external_providers()
    init_pool(args.max_workers)
    ensure_schema()
    symbols = load_symbols(LOGGER, as_of_date=latest_date.isoformat(), limit=args.limit, offset=args.offset)

    LOGGER.info(
        "🚀 开始同步日线，目标区间 %s -> %s，标的数 %s，修复窗口 %s 天",
        trade_dates[0],
        trade_dates[-1],
        len(symbols),
        args.repair_days,
    )

    total_rows = 0
    finished = 0
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(process_symbol, symbol, trade_dates, args.repair_days): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            finished += 1
            try:
                _, rows = future.result()
                total_rows += rows
                if rows:
                    LOGGER.info("[%s/%s] %s +%s", finished, len(symbols), symbol.name, rows)
            except Exception as exc:
                LOGGER.error("[%s/%s] %s 失败: %s", finished, len(symbols), symbol.name, exc)

    LOGGER.info("🎉 日线同步完成，共写入/更新 %s 条", f"{total_rows:,}")


if __name__ == "__main__":
    main()
