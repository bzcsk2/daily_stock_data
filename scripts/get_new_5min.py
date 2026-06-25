#!/usr/bin/env python3
"""Incremental 5-minute K-line sync with repair for malformed timestamps."""

from __future__ import annotations

import argparse
import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress

import baostock as bs
import pandas as pd
from psycopg2.extras import execute_values
from psycopg2.pool import ThreadedConnectionPool

from kline_common import (
    DEFAULT_DB_CONFIG,
    SymbolInfo,
    fetch_trade_dates,
    latest_trade_date,
    load_symbols,
    setup_logging,
    split_date_ranges,
)
from storage_common import read_csv_table, replace_csv_slice, use_csv, use_postgres

LOGGER = setup_logging("./logs/get_new_5min.log")

START_DATE = "2019-01-01"
STOCK_TABLE = "min5_ohlcv"
INDEX_TABLE = "index_min5"
DEFAULT_WORKERS = 1
DEFAULT_BATCH_SIZE = 3000
DEFAULT_SEGMENT_DAYS = 15
DEFAULT_LOOKBACK_DAYS = 240
MIN_COMPLETE_DAY_ROWS = {
    STOCK_TABLE: 10_000,
    INDEX_TABLE: 100,
}

VALID_TIMES = {
    f"{hour:02d}:{minute:02d}:00"
    for hour, minute in (
        [(9, minute) for minute in range(35, 60, 5)]
        + [(10, minute) for minute in range(0, 60, 5)]
        + [(11, minute) for minute in range(0, 31, 5)]
        + [(13, minute) for minute in range(5, 60, 5)]
        + [(14, minute) for minute in range(0, 60, 5)]
        + [(15, 0)]
    )
}

pool: ThreadedConnectionPool | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="增量同步 5 分钟 K 线")
    parser.add_argument("--start-date", default=START_DATE, help="初始起始日期")
    parser.add_argument("--end-date", default=None, help="结束日期，默认最近交易日")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="判断全局完整日时往前看多少自然日")
    parser.add_argument("--segment-days", type=int, default=DEFAULT_SEGMENT_DAYS, help="单次请求覆盖的自然日跨度")
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

            cur.execute(f"CREATE UNIQUE INDEX uk_{table}_time_symbol ON {table} (time, symbol)")
        conn.commit()
    finally:
        put_conn(conn)


def ensure_schema() -> None:
    if not use_postgres():
        return

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for table in (STOCK_TABLE, INDEX_TABLE):
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        time TIMESTAMP NOT NULL,
                        symbol VARCHAR(20) NOT NULL,
                        open NUMERIC(20,4),
                        high NUMERIC(20,4),
                        low NUMERIC(20,4),
                        close NUMERIC(20,4),
                        volume NUMERIC(20,4),
                        turnover NUMERIC(20,4)
                    )
                    """
                )
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS amount NUMERIC(15,2)")
        conn.commit()
        ensure_unique_time_symbol(STOCK_TABLE)
        ensure_unique_time_symbol(INDEX_TABLE)
        LOGGER.info("✅ 5 分钟表结构检查完成")
    finally:
        put_conn(conn)


def find_latest_complete_day(table: str, start_date: dt.date, end_date: dt.date) -> dt.date | None:
    if use_csv():
        df = read_csv_table(table, parse_dates=["time"])
        if df.empty or "time" not in df.columns:
            if not use_postgres():
                return None
        else:
            times = pd.to_datetime(df["time"], errors="coerce")
            mask = (times >= pd.Timestamp(start_date)) & (times < pd.Timestamp(end_date + dt.timedelta(days=1)))
            stats = times[mask].dt.date.value_counts()
            stats = stats[stats >= MIN_COMPLETE_DAY_ROWS[table]]
            if not stats.empty:
                return max(stats.index)
            if not use_postgres():
                return None

    if not use_postgres():
        return None

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT day
                FROM (
                    SELECT time::date AS day, COUNT(*) AS row_count
                    FROM {table}
                    WHERE time >= %s
                      AND time < %s
                    GROUP BY 1
                ) stats
                WHERE row_count >= %s
                ORDER BY day DESC
                LIMIT 1
                """,
                (start_date, end_date + dt.timedelta(days=1), MIN_COMPLETE_DAY_ROWS[table]),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        put_conn(conn)


def build_sync_dates(table: str, start_date: dt.date, end_date: dt.date, lookback_days: int) -> list[dt.date]:
    lookback_start = max(start_date, end_date - dt.timedelta(days=lookback_days))
    latest_complete = find_latest_complete_day(table, lookback_start, end_date)

    if latest_complete is None:
        sync_start = start_date
    else:
        sync_start = latest_complete + dt.timedelta(days=1)

    if sync_start > end_date:
        LOGGER.info("📊 %s 5 分钟数据已覆盖到 %s，无需补数", table, end_date)
        return []

    trade_dates = fetch_trade_dates(sync_start.isoformat(), end_date.isoformat())
    LOGGER.info(
        "📊 %s 5 分钟补数起点: %s（最近完整日=%s）",
        table,
        sync_start,
        latest_complete,
    )
    return trade_dates


def normalize_intraday_timestamp(date_text: str, raw_time: str) -> str | None:
    digits = "".join(ch for ch in str(raw_time).strip() if ch.isdigit())
    if len(digits) >= 14:
        hhmmss = digits[8:14]
    elif len(digits) >= 6:
        hhmmss = digits[-6:]
    else:
        return None
    return f"{date_text} {hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"


def filter_valid_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    timestamps = [
        normalize_intraday_timestamp(date_text, raw_time)
        for date_text, raw_time in zip(df["date"], df["time"], strict=False)
    ]
    df["time"] = pd.to_datetime(timestamps, errors="coerce")
    df = df.dropna(subset=["time"])
    df = df[df["time"].dt.strftime("%H:%M:%S").isin(VALID_TIMES)]
    return df


def _bs_login() -> None:
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")


def download_segment(symbol: SymbolInfo, start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    rs = bs.query_history_k_data_plus(
        symbol.baostock_code,
        "date,time,code,open,high,low,close,volume,amount",
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        frequency="5",
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
    numeric_columns = ["open", "high", "low", "close", "volume", "amount"]
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
    df = filter_valid_bars(df)
    if df.empty:
        return df

    df = df.dropna(subset=["open", "high", "low", "close", "volume", "amount"])
    df = df[(df[["open", "high", "low", "close", "volume", "amount"]] != 0).all(axis=1)]
    df = df.rename(columns={"code": "symbol", "amount": "turnover"})
    df["amount"] = df["turnover"]
    return df[["time", "symbol", "open", "high", "low", "close", "volume", "turnover", "amount"]]


def replace_segment(table: str, symbol: str, start_date: dt.date, end_date: dt.date, df: pd.DataFrame) -> int:
    written = 0
    if use_csv():
        written = replace_csv_slice(
            df,
            table,
            ["time", "symbol"],
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            time_column="time",
        )
    if not use_postgres():
        return written

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM {table}
                WHERE symbol = %s
                  AND time >= %s
                  AND time < %s
                """,
                (symbol, start_date, end_date + dt.timedelta(days=1)),
            )
            if not df.empty:
                rows = [tuple(row) for row in df.itertuples(index=False, name=None)]
                sql = f"""
                    INSERT INTO {table}
                    (time, symbol, open, high, low, close, volume, turnover, amount)
                    VALUES %s
                    ON CONFLICT (time, symbol) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        turnover = EXCLUDED.turnover,
                        amount = EXCLUDED.amount
                """
                execute_values(cur, sql, rows, page_size=DEFAULT_BATCH_SIZE)
        conn.commit()
        return len(df)
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def process_symbol(
    symbol: SymbolInfo,
    stock_ranges: list[tuple[dt.date, dt.date]],
    index_ranges: list[tuple[dt.date, dt.date]],
) -> tuple[str, int]:
    ranges = index_ranges if symbol.is_index else stock_ranges
    if not ranges:
        return symbol.baostock_code, 0

    table = INDEX_TABLE if symbol.is_index else STOCK_TABLE
    inserted = 0
    for start_date, end_date in ranges:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                _bs_login()
                try:
                    df = download_segment(symbol, start_date, end_date)
                    inserted += replace_segment(table, symbol.db_symbol, start_date, end_date, df)
                    time.sleep(0.2)
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
    end_date = dt.datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else latest_trade_date()
    start_date = dt.datetime.strptime(args.start_date, "%Y-%m-%d").date()
    if start_date > end_date:
        raise RuntimeError("start-date 不能晚于 end-date")

    init_pool(args.max_workers)
    ensure_schema()

    symbols = load_symbols(LOGGER, as_of_date=end_date.isoformat(), limit=args.limit, offset=args.offset)
    need_stock = any(not symbol.is_index for symbol in symbols)
    need_index = any(symbol.is_index for symbol in symbols)

    stock_dates = build_sync_dates(STOCK_TABLE, start_date, end_date, args.lookback_days) if need_stock else []
    index_dates = build_sync_dates(INDEX_TABLE, start_date, end_date, args.lookback_days) if need_index else []
    if not stock_dates and not index_dates:
        LOGGER.info("✅ 5 分钟数据已是最新，无需补数")
        return

    stock_ranges = split_date_ranges(stock_dates, args.segment_days)
    index_ranges = split_date_ranges(index_dates, args.segment_days)
    LOGGER.info(
        "🚀 开始同步 5 分钟 K 线，股票待补 %s 天/%s 段，指数待补 %s 天/%s 段，标的数 %s",
        len(stock_dates),
        len(stock_ranges),
        len(index_dates),
        len(index_ranges),
        len(symbols),
    )

    total_rows = 0
    finished = 0
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(process_symbol, symbol, stock_ranges, index_ranges): symbol
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

    LOGGER.info("🎉 5 分钟同步完成，共写入/更新 %s 条", f"{total_rows:,}")


if __name__ == "__main__":
    main()
