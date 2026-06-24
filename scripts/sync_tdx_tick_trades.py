#!/usr/bin/env python3
"""Sync pytdx tick-by-tick trades into PostgreSQL."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import psycopg2
from psycopg2.extras import Json, execute_values

from kline_common import DEFAULT_DB_CONFIG, setup_logging
from storage_common import read_csv_table, use_csv, use_postgres, write_csv_table
from tdx_common import TdxSymbol, connect_hq_api, load_tdx_stock_universe

LOGGER = setup_logging("./logs/tdx_tick_trades.log")
TABLE_NAME = "stock_tick_trades_tdx"
SESSION_AM = "am"
SESSION_PM = "pm"
PAGE_SIZE_DEFAULT = 1800


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 pytdx 逐笔成交")
    parser.add_argument("--session", choices=[SESSION_AM, SESSION_PM], required=True, help="本次任务目标时段")
    parser.add_argument("--backfill-am", action="store_true", help="下午任务顺手重刷上午数据")
    parser.add_argument("--date", help="历史回放日期，格式 YYYYMMDD 或 YYYY-MM-DD；不传则抓当日实时逐笔")
    parser.add_argument("--workers", type=int, default=4, help="并发 worker 数")
    parser.add_argument("--chunk-size", type=int, default=25, help="每个 worker 处理的 symbol 数")
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE_DEFAULT, help="逐笔接口分页大小")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 个标的，便于测试")
    parser.add_argument("--offset", type=int, default=0, help="标的偏移，便于测试")
    return parser.parse_args()


def get_conn():
    return psycopg2.connect(**DEFAULT_DB_CONFIG)


def parse_trade_date(value: str | None) -> dt.date:
    if not value:
        return dt.date.today()
    text = value.strip()
    if "-" in text:
        return dt.datetime.strptime(text, "%Y-%m-%d").date()
    return dt.datetime.strptime(text, "%Y%m%d").date()


def month_bounds(day: dt.date) -> tuple[dt.date, dt.date]:
    month_start = day.replace(day=1)
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    return month_start, next_month


def ensure_schema(trade_date: dt.date) -> None:
    if not use_postgres():
        return

    conn = get_conn()
    partition_name = f"{TABLE_NAME}_{trade_date.strftime('%Y%m')}"
    month_start, next_month = month_bounds(trade_date)
    try:
        with conn, conn.cursor() as cur:
            # Avoid duplicate-type races when two bootstrap runs happen together.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (TABLE_NAME,))
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    trade_date DATE NOT NULL,
                    symbol VARCHAR(16) NOT NULL,
                    session_name VARCHAR(8) NOT NULL,
                    tick_index INTEGER NOT NULL,
                    trade_time TIME NOT NULL,
                    price NUMERIC(18, 4) NOT NULL,
                    vol BIGINT,
                    num BIGINT,
                    buyorsell INTEGER,
                    source_mode VARCHAR(16) NOT NULL,
                    raw_payload JSONB NOT NULL,
                    fetched_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (trade_date, symbol, session_name, tick_index)
                ) PARTITION BY RANGE (trade_date)
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {partition_name}
                PARTITION OF {TABLE_NAME}
                FOR VALUES FROM (%s) TO (%s)
                """,
                (month_start, next_month),
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_symbol_date
                ON {TABLE_NAME} (symbol, trade_date DESC)
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_date_session
                ON {TABLE_NAME} (trade_date DESC, session_name, symbol)
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_fetched_at
                ON {TABLE_NAME} (fetched_at DESC)
                """
            )
        LOGGER.info("✅ pytdx 逐笔表结构检查完成: %s / %s", TABLE_NAME, partition_name)
    finally:
        conn.close()


def chunked(items: list[TdxSymbol], size: int) -> list[list[TdxSymbol]]:
    return [items[idx : idx + size] for idx in range(0, len(items), size)]


def classify_session(trade_time: dt.time) -> str | None:
    if trade_time <= dt.time(11, 30):
        return SESSION_AM
    if trade_time >= dt.time(13, 0):
        return SESSION_PM
    return None


def parse_time_text(value: str) -> dt.time | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.time.fromisoformat(text)
    except ValueError:
        return None


def to_int_or_none(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_all_ticks(api, item: TdxSymbol, trade_date: dt.date, page_size: int, *, use_history: bool) -> list[dict]:
    request_count = min(page_size, PAGE_SIZE_DEFAULT)
    pages: list[list[dict]] = []
    start = 0
    trade_date_int = int(trade_date.strftime("%Y%m%d"))
    while True:
        if use_history:
            payload = api.get_history_transaction_data(item.market, item.code, start, request_count, trade_date_int)
        else:
            payload = api.get_transaction_data(item.market, item.code, start, request_count)
        frame = api.to_df(payload)
        if frame is None or frame.empty:
            break
        rows = frame.to_dict("records")
        pages.append(rows)
        if len(rows) < request_count:
            break
        start += len(rows)

    merged: list[dict] = []
    for page in reversed(pages):
        merged.extend(page)
    return merged


def build_rows_for_symbol(
    symbol: str,
    trade_date: dt.date,
    ticks: list[dict],
    fetched_at: dt.datetime,
    *,
    source_mode: str,
    session_name: str,
    backfill_am: bool,
) -> dict[str, list[tuple]]:
    target_sessions = {session_name}
    if session_name == SESSION_PM and backfill_am:
        target_sessions.add(SESSION_AM)

    grouped: dict[str, list[tuple]] = {name: [] for name in sorted(target_sessions)}
    counters = {name: 0 for name in target_sessions}

    for payload in ticks:
        trade_time = parse_time_text(payload.get("time"))
        if trade_time is None:
            continue
        actual_session = classify_session(trade_time)
        if actual_session not in target_sessions:
            continue

        counters[actual_session] += 1
        grouped[actual_session].append(
            (
                trade_date,
                symbol,
                actual_session,
                counters[actual_session],
                trade_time,
                payload.get("price"),
                to_int_or_none(payload.get("vol")),
                to_int_or_none(payload.get("num")),
                to_int_or_none(payload.get("buyorsell")),
                source_mode,
                Json(payload),
                fetched_at,
                fetched_at,
            )
        )
    return grouped


def fetch_chunk(
    symbols: list[TdxSymbol],
    trade_date: dt.date,
    fetched_at: dt.datetime,
    *,
    page_size: int,
    use_history: bool,
    session_name: str,
    backfill_am: bool,
) -> tuple[list[tuple], list[str], set[str], int]:
    rows: list[tuple] = []
    successful_symbols: list[str] = []
    failed_symbols = 0
    target_sessions = {session_name}
    if session_name == SESSION_PM and backfill_am:
        target_sessions.add(SESSION_AM)

    with connect_hq_api(LOGGER) as api:
        for item in symbols:
            try:
                ticks = fetch_all_ticks(api, item, trade_date, page_size, use_history=use_history)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("⚠ 抓取逐笔失败 %s，尝试重连后重试一次: %s", item.symbol, exc)
                try:
                    with connect_hq_api(LOGGER) as retry_api:
                        ticks = fetch_all_ticks(
                            retry_api,
                            item,
                            trade_date,
                            page_size,
                            use_history=use_history,
                        )
                except Exception as retry_exc:  # noqa: BLE001
                    LOGGER.error("❌ 抓取逐笔失败 %s，已跳过: %s", item.symbol, retry_exc)
                    failed_symbols += 1
                    continue

            grouped = build_rows_for_symbol(
                item.symbol,
                trade_date,
                ticks,
                fetched_at,
                source_mode="history" if use_history else "current",
                session_name=session_name,
                backfill_am=backfill_am,
            )
            for session_rows in grouped.values():
                rows.extend(session_rows)
            successful_symbols.append(item.symbol)

    return rows, successful_symbols, target_sessions, failed_symbols


def save_rows(rows: list[tuple], trade_date: dt.date, symbols: list[str], sessions: set[str]) -> tuple[int, int]:
    if not symbols:
        return 0, 0

    deleted = 0
    inserted = 0
    if use_csv():
        columns = [
            "trade_date", "symbol", "session_name", "tick_index", "trade_time",
            "price", "vol", "num", "buyorsell", "source_mode", "raw_payload",
            "fetched_at", "updated_at",
        ]
        existing = read_csv_table(TABLE_NAME, parse_dates=["trade_date"])
        if not existing.empty:
            trade_dates = pd.to_datetime(existing["trade_date"], errors="coerce").dt.date
            mask = (
                trade_dates.eq(trade_date)
                & existing["symbol"].isin(symbols)
                & existing["session_name"].isin(sorted(sessions))
            )
            deleted = int(mask.sum())
            existing = existing[~mask]

        csv_rows = []
        for row in rows:
            values = list(row)
            values[10] = json.dumps(values[10].adapted, ensure_ascii=False)
            csv_rows.append(values)
        new_df = pd.DataFrame(csv_rows, columns=columns)
        output = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
        if not output.empty:
            output = output.drop_duplicates(
                subset=["trade_date", "symbol", "session_name", "tick_index"],
                keep="last",
            ).sort_values(["trade_date", "symbol", "session_name", "tick_index"])
        write_csv_table(output, TABLE_NAME)
        inserted = len(rows)

    if not use_postgres():
        return deleted, inserted

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM {TABLE_NAME}
                WHERE trade_date = %s
                  AND symbol = ANY(%s)
                  AND session_name = ANY(%s)
                """,
                (trade_date, symbols, sorted(sessions)),
            )
            deleted = cur.rowcount

            if rows:
                sql = f"""
                    INSERT INTO {TABLE_NAME} (
                        trade_date, symbol, session_name, tick_index, trade_time,
                        price, vol, num, buyorsell, source_mode, raw_payload,
                        fetched_at, updated_at
                    )
                    VALUES %s
                """
                execute_values(cur, sql, rows, page_size=2000)
                inserted = len(rows)
        return deleted, inserted
    finally:
        conn.close()


def run_sync(
    *,
    trade_date: dt.date,
    use_history: bool,
    session_name: str,
    backfill_am: bool,
    workers: int,
    chunk_size: int,
    page_size: int,
    limit: int | None = None,
    offset: int = 0,
    symbols: list[TdxSymbol] | None = None,
) -> dict[str, int]:
    ensure_schema(trade_date)

    effective_symbols = list(symbols) if symbols is not None else load_tdx_stock_universe(LOGGER)
    if offset:
        effective_symbols = effective_symbols[offset:]
    if limit is not None:
        effective_symbols = effective_symbols[:limit]

    fetched_at = dt.datetime.now(dt.timezone.utc)
    total_rows = 0
    total_symbols = 0
    total_deleted = 0
    total_failed = 0
    session_names = [session_name]
    if session_name == SESSION_PM and backfill_am:
        session_names.insert(0, SESSION_AM)

    LOGGER.info(
        "🚀 开始同步 pytdx 逐笔，trade_date=%s mode=%s sessions=%s symbols=%s workers=%s",
        trade_date.isoformat(),
        "history" if use_history else "current",
        ",".join(session_names),
        len(effective_symbols),
        workers,
    )

    batches = chunked(effective_symbols, chunk_size)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                fetch_chunk,
                batch,
                trade_date,
                fetched_at,
                page_size=page_size,
                use_history=use_history,
                session_name=session_name,
                backfill_am=backfill_am,
            ): idx
            for idx, batch in enumerate(batches, start=1)
        }
        for future in as_completed(futures):
            idx = futures[future]
            rows, successful_symbols, sessions, failed_symbols = future.result()
            deleted, inserted = save_rows(rows, trade_date, successful_symbols, sessions)
            total_rows += inserted
            total_symbols += len(successful_symbols)
            total_deleted += deleted
            total_failed += failed_symbols
            LOGGER.info(
                "[batch %s/%s] processed_symbols=%s failed_symbols=%s deleted_rows=%s written_rows=%s total_rows=%s",
                idx,
                len(batches),
                len(successful_symbols),
                failed_symbols,
                deleted,
                inserted,
                total_rows,
            )

    LOGGER.info(
        "✅ pytdx 逐笔同步完成，trade_date=%s processed_symbols=%s failed_symbols=%s deleted_rows=%s written_rows=%s",
        trade_date.isoformat(),
        total_symbols,
        total_failed,
        total_deleted,
        total_rows,
    )
    return {
        "processed_symbols": total_symbols,
        "failed_symbols": total_failed,
        "deleted_rows": total_deleted,
        "written_rows": total_rows,
    }


def main() -> None:
    args = parse_args()
    trade_date = parse_trade_date(args.date)
    use_history = args.date is not None
    run_sync(
        trade_date=trade_date,
        use_history=use_history,
        session_name=args.session,
        backfill_am=args.backfill_am,
        workers=args.workers,
        chunk_size=args.chunk_size,
        page_size=args.page_size,
        limit=args.limit,
        offset=args.offset,
    )


if __name__ == "__main__":
    main()
