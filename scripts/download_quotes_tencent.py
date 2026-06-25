#!/usr/bin/env python3
"""Tencent snapshot collector writing to unified snapshot storage."""

from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import easyquotation
import pandas as pd
import pytz
from psycopg2.extras import execute_values
from snapshot_unified_common import UNIFIED_TABLE, create_partition, ensure_unified_schema, get_conn
from storage_common import append_upsert_csv, use_csv, use_postgres, write_csv_table

LOGGER = logging.getLogger("download_quotes_tencent")
TZ = pytz.timezone("Asia/Shanghai")

COLUMN_MAPPING = {
    "name": "name",
    "now": "now",
    "close": "close",
    "open": "open",
    "high": "high",
    "low": "low",
    "buy": "buy",
    "sell": "sell",
    "volume": "volume",
    "turnover": "turnover",
    "涨跌": "change_amount",
    "涨跌(%)": "change_percent",
    "成交量(手)": "volume_lots",
    "成交额(万)": "amount_wan",
    "振幅": "amplitude",
    "总市值": "market_cap",
    "流通市值": "circulating_cap",
    "PE": "pe",
    "PB": "pb",
    "涨停价": "limit_up_price",
    "跌停价": "limit_down_price",
    "量比": "volume_ratio",
    "委差": "weicha",
    "均价": "avg_price",
}

UNIFIED_COLUMNS = [
    "source",
    "source_snapshot_id",
    "symbol",
    "name",
    "snapshot_time",
    "price",
    "open",
    "close",
    "high",
    "low",
    "buy",
    "sell",
    "amount",
    "volume_shares",
    "volume_lots",
    "change_amount",
    "change_percent",
    "amplitude",
    "market_cap",
    "circulating_cap",
    "pe",
    "pb",
    "limit_up_price",
    "limit_down_price",
    "is_limit_up",
    "is_limit_down",
    "volume_ratio",
    "weicha",
    "avg_price",
    "bid1",
    "bid1_volume",
    "bid2",
    "bid2_volume",
    "bid3",
    "bid3_volume",
    "bid4",
    "bid4_volume",
    "bid5",
    "bid5_volume",
    "ask1",
    "ask1_volume",
    "ask2",
    "ask2_volume",
    "ask3",
    "ask3_volume",
    "ask4",
    "ask4_volume",
    "ask5",
    "ask5_volume",
    "raw_volume",
    "raw_turnover",
    "raw_amount_wan",
]

LATEST_TICK_COLUMNS = [
    "symbol",
    "name",
    "updated_at",
    "now",
    "open",
    "close",
    "high",
    "low",
    "avg_price",
    "change_amount",
    "change_percent",
    "volume_lots",
    "amount_wan",
    "turnover",
    "market_cap",
    "circulating_cap",
    "pe",
    "pb",
    "volume_ratio",
    "amplitude",
    "limit_up_price",
    "limit_down_price",
    "is_limit_up",
    "is_limit_down",
    "seal_amount_wan",
    "weicha",
    "bid1",
    "bid1_volume",
    "bid2",
    "bid2_volume",
    "bid3",
    "bid3_volume",
    "bid4",
    "bid4_volume",
    "bid5",
    "bid5_volume",
    "ask1",
    "ask1_volume",
    "ask2",
    "ask2_volume",
    "ask3",
    "ask3_volume",
    "ask4",
    "ask4_volume",
    "ask5",
    "ask5_volume",
]


def setup_logging() -> None:
    Path("./logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("./logs/download_quotes_tencent.log"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def is_trading_time() -> bool:
    now = datetime.now(TZ)
    if now.weekday() >= 5:
        return False
    current = now.time()
    return (
        datetime.strptime("09:15", "%H:%M").time() <= current <= datetime.strptime("11:30", "%H:%M").time()
        or datetime.strptime("13:00", "%H:%M").time() <= current <= datetime.strptime("15:00", "%H:%M").time()
    )


def table_exists(conn, table_name: str) -> bool:
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
            (table_name,),
        )
        return cur.fetchone()[0]


def normalize_market_data(market_data: dict) -> pd.DataFrame:
    df = pd.concat(
        [pd.Series(list(market_data.keys()), name="symbol"), pd.DataFrame(list(market_data.values()))],
        axis=1,
    )
    df.rename(columns=COLUMN_MAPPING, inplace=True)

    numeric_candidates = [
        "now",
        "open",
        "close",
        "high",
        "low",
        "buy",
        "sell",
        "volume",
        "turnover",
        "change_amount",
        "change_percent",
        "volume_lots",
        "amount_wan",
        "amplitude",
        "market_cap",
        "circulating_cap",
        "pe",
        "pb",
        "limit_up_price",
        "limit_down_price",
        "volume_ratio",
        "weicha",
        "avg_price",
        "bid1",
        "bid1_volume",
        "bid2",
        "bid2_volume",
        "bid3",
        "bid3_volume",
        "bid4",
        "bid4_volume",
        "bid5",
        "bid5_volume",
        "ask1",
        "ask1_volume",
        "ask2",
        "ask2_volume",
        "ask3",
        "ask3_volume",
        "ask4",
        "ask4_volume",
        "ask5",
        "ask5_volume",
    ]
    for column in numeric_candidates:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    now_time = datetime.now(TZ)
    df["snapshot_time"] = now_time
    df["source"] = "tencent"
    df["source_snapshot_id"] = None
    df["price"] = df.get("now")
    df["amount"] = df.get("volume")
    df["volume_shares"] = df.get("volume_lots").fillna(0) * 100 if "volume_lots" in df.columns else None
    if "turnover" in df.columns:
        df["volume_shares"] = df["volume_shares"].where(df["volume_shares"] > 0, df["turnover"])
    df["is_limit_up"] = (
        (df.get("price", 0).fillna(0) > 0)
        & (df.get("limit_up_price", 0).fillna(0) > 0)
        & (df.get("price", 0).fillna(0) >= df.get("limit_up_price", 0).fillna(0))
    )
    df["is_limit_down"] = (
        (df.get("price", 0).fillna(0) > 0)
        & (df.get("limit_down_price", 0).fillna(0) > 0)
        & (df.get("price", 0).fillna(0) <= df.get("limit_down_price", 0).fillna(0))
    )
    df["raw_volume"] = df.get("volume")
    df["raw_turnover"] = df.get("turnover")
    df["raw_amount_wan"] = df.get("amount_wan")

    for column in UNIFIED_COLUMNS:
        if column not in df.columns:
            df[column] = None
    return df


def save_unified_snapshots(conn, df: pd.DataFrame) -> int:
    if use_csv():
        written = append_upsert_csv(
            df[UNIFIED_COLUMNS],
            UNIFIED_TABLE,
            ["snapshot_time", "source", "symbol"],
            parse_dates=["snapshot_time"],
        )
        if not use_postgres():
            return written

    rows = [tuple(row) for row in df[UNIFIED_COLUMNS].itertuples(index=False, name=None)]
    update_columns = [column for column in UNIFIED_COLUMNS if column not in {"source", "source_snapshot_id", "symbol", "snapshot_time"}]
    update_clause = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""
            INSERT INTO {UNIFIED_TABLE} ({",".join(UNIFIED_COLUMNS)})
            VALUES %s
            ON CONFLICT (snapshot_time, source, symbol) DO UPDATE SET
                {update_clause}
            """,
            rows,
            page_size=1000,
        )
    conn.commit()
    return len(rows)


def upsert_latest_tick(conn, df: pd.DataFrame) -> None:
    latest_df = df.copy()
    latest_df["updated_at"] = latest_df["snapshot_time"]
    latest_df["now"] = latest_df["price"]
    latest_df["seal_amount_wan"] = (
        latest_df.get("ask1_volume", 0).fillna(0) * latest_df.get("ask1", 0).fillna(0) * 100 / 10000
    ).where(latest_df["is_limit_up"], 0)
    for column in LATEST_TICK_COLUMNS:
        if column not in latest_df.columns:
            latest_df[column] = None

    if use_csv():
        output = latest_df[LATEST_TICK_COLUMNS].drop_duplicates(subset=["symbol"], keep="last")
        write_csv_table(output.sort_values(["symbol"]), "latest_tick")

    if not use_postgres() or not table_exists(conn, "latest_tick"):
        return

    rows = [tuple(row) for row in latest_df[LATEST_TICK_COLUMNS].itertuples(index=False, name=None)]
    update_columns = [column for column in LATEST_TICK_COLUMNS if column != "symbol"]
    update_clause = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""
            INSERT INTO latest_tick ({",".join(LATEST_TICK_COLUMNS)})
            VALUES %s
            ON CONFLICT (symbol) DO UPDATE SET
                {update_clause}
            """,
            rows,
            page_size=1000,
        )
    conn.commit()


def collect_snapshots() -> None:
    if not is_trading_time():
        return

    started = time.time()
    try:
        quotation = easyquotation.use("tencent")
        market_data = quotation.market_snapshot()
        if not market_data:
            LOGGER.warning("⚠️ 获取到空快照")
            return

        df = normalize_market_data(market_data)
        if use_postgres():
            with get_conn() as conn:
                create_partition(conn, datetime.now(TZ).date())
                written = save_unified_snapshots(conn, df)
                upsert_latest_tick(conn, df)
        else:
            written = save_unified_snapshots(None, df)
            upsert_latest_tick(None, df)
        LOGGER.info("✅ %s 写入统一快照 %s 条，用时 %.2fs", df['snapshot_time'].iloc[0].strftime("%H:%M:%S"), written, time.time() - started)
    except Exception as exc:
        LOGGER.error("❌ 腾讯快照采集失败: %s", exc)
        LOGGER.error(traceback.format_exc())


def main() -> None:
    setup_logging()
    if use_postgres():
        with get_conn() as conn:
            ensure_unified_schema(conn)
            today = datetime.now(TZ).date()
            create_partition(conn, today)
            create_partition(conn, today + timedelta(days=1))

    LOGGER.info("🚀 腾讯快照统一表采集启动")
    while True:
        collect_snapshots()
        time.sleep(3)


if __name__ == "__main__":
    main()
