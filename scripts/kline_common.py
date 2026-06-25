#!/usr/bin/env python3
"""Common helpers for daily/5min K-line sync scripts."""

from __future__ import annotations

import datetime as dt
import logging
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import baostock as bs
import baostock.common.contants as _bs_cons
import psycopg2

from storage_common import read_csv_table, use_postgres

_bs_cons.BAOSTOCK_SERVER_IP = "public-api.baostock.com"

DEFAULT_DB_CONFIG = {
    "host": os.getenv("MARKET_DB_HOST", "localhost"),
    "port": int(os.getenv("MARKET_DB_PORT", "5432")),
    "dbname": os.getenv("MARKET_DB_NAME", "market"),
    "user": os.getenv("MARKET_DB_USER", "postgres"),
    "password": os.getenv("MARKET_DB_PASSWORD", ""),
}

A_SHARE_PREFIXES = (
    "sh.600",
    "sh.601",
    "sh.603",
    "sh.605",
    "sh.688",
    "sz.000",
    "sz.001",
    "sz.002",
    "sz.003",
    "sz.300",
    "sz.301",
)

INDEX_CODES = {
    "sh.000001",
    "sh.000016",
    "sh.000300",
    "sh.000688",
    "sh.000852",
    "sh.000905",
    "sz.399001",
    "sz.399005",
    "sz.399006",
    "sz.399300",
}

INDEX_NAMES = {
    "sh.000001": "上证综合指数",
    "sh.000016": "上证50指数",
    "sh.000300": "沪深300指数",
    "sh.000688": "科创50指数",
    "sh.000852": "中证1000指数",
    "sh.000905": "中证500指数",
    "sz.399001": "深证成份指数(价格)",
    "sz.399005": "中小企业100指数",
    "sz.399006": "创业板指数(价格)",
    "sz.399300": "沪深300指数",
}

BAOSTOCK_RETRY_ATTEMPTS = 3
BAOSTOCK_RETRY_BASE_SLEEP = 1.0


@dataclass(frozen=True)
class SymbolInfo:
    baostock_code: str
    db_symbol: str
    name: str
    is_index: bool


def setup_logging(log_path: str) -> logging.Logger:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler()],
        force=True,
    )
    return logging.getLogger(Path(log_path).stem)


def is_a_share(code: str) -> bool:
    return code.startswith(A_SHARE_PREFIXES)


def is_supported_index(code: str) -> bool:
    return code in INDEX_CODES


def get_conn():
    if not use_postgres():
        raise RuntimeError("PostgreSQL backend is disabled; set STORAGE_BACKEND=postgres or both to use it")
    return psycopg2.connect(**DEFAULT_DB_CONFIG)


def load_env_file(path: str) -> None:
    if not path or not Path(path).is_file():
        return

    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def ts_code_to_baostock(code: str) -> str | None:
    code = code.strip().upper()
    if code.endswith(".SH"):
        return f"sh.{code[:-3]}"
    if code.endswith(".SZ"):
        return f"sz.{code[:-3]}"
    return None


def baostock_to_ts_code(code: str) -> str | None:
    code = code.strip().lower()
    if code.startswith("sh."):
        return f"{code[3:].upper()}.SH"
    if code.startswith("sz."):
        return f"{code[3:].upper()}.SZ"
    return None


def _load_symbols_from_local_tables(
    logger: logging.Logger,
    *,
    include_indices: bool,
    limit: int | None,
    offset: int,
) -> list[SymbolInfo]:
    csv_result = _load_symbols_from_csv(logger, include_indices=include_indices, limit=limit, offset=offset)
    if csv_result:
        return csv_result

    if not use_postgres():
        return []

    table_queries = [
        (
            "stock_basic_tickflow",
            """
            SELECT symbol, COALESCE(name, symbol)
            FROM stock_basic_tickflow
            ORDER BY symbol
            """,
        ),
        (
            "stock_basic_tushare",
            """
            SELECT ts_code, COALESCE(name, ts_code)
            FROM stock_basic_tushare
            WHERE list_status = 'L'
            ORDER BY ts_code
            """,
        ),
    ]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for source_name, sql in table_queries:
                try:
                    cur.execute(sql)
                    rows = cur.fetchall()
                except Exception:
                    conn.rollback()
                    continue

                result: list[SymbolInfo] = []
                seen: set[str] = set()
                for ts_code, name in rows:
                    baostock_code = ts_code_to_baostock(ts_code)
                    if not baostock_code or not is_a_share(baostock_code):
                        continue
                    if baostock_code in seen:
                        continue
                    seen.add(baostock_code)
                    result.append(SymbolInfo(baostock_code, baostock_code, name, False))

                if include_indices:
                    for index_code in sorted(INDEX_CODES):
                        result.append(SymbolInfo(index_code, index_code, INDEX_NAMES.get(index_code, index_code), True))

                if not result:
                    continue

                result.sort(key=lambda item: (item.is_index, item.baostock_code))
                if offset:
                    result = result[offset:]
                if limit is not None:
                    result = result[:limit]

                stock_count = sum(1 for item in result if not item.is_index)
                index_count = len(result) - stock_count
                logger.info(
                    "✅ 从本地表 %s 加载 %s 个标的（股票 %s，只跟踪指数 %s）",
                    source_name,
                    len(result),
                    stock_count,
                    index_count,
                )
                return result
    finally:
        conn.close()

    return []


def _load_symbols_from_csv(
    logger: logging.Logger,
    *,
    include_indices: bool,
    limit: int | None,
    offset: int,
) -> list[SymbolInfo]:
    table_columns = [
        ("stock_basic_tickflow", "symbol", "name"),
        ("stock_basic_tushare", "ts_code", "name"),
    ]

    for table, symbol_column, name_column in table_columns:
        df = read_csv_table(table)
        if df.empty or symbol_column not in df.columns:
            continue
        if table == "stock_basic_tushare" and "list_status" in df.columns:
            df = df[df["list_status"] == "L"]

        result: list[SymbolInfo] = []
        seen: set[str] = set()
        for row in df.to_dict("records"):
            baostock_code = ts_code_to_baostock(str(row.get(symbol_column, "")))
            if not baostock_code or not is_a_share(baostock_code) or baostock_code in seen:
                continue
            seen.add(baostock_code)
            name = str(row.get(name_column) or baostock_code)
            result.append(SymbolInfo(baostock_code, baostock_code, name, False))

        if include_indices:
            for index_code in sorted(INDEX_CODES):
                result.append(SymbolInfo(index_code, index_code, INDEX_NAMES.get(index_code, index_code), True))

        if not result:
            continue
        result.sort(key=lambda item: (item.is_index, item.baostock_code))
        if offset:
            result = result[offset:]
        if limit is not None:
            result = result[:limit]

        stock_count = sum(1 for item in result if not item.is_index)
        index_count = len(result) - stock_count
        logger.info(
            "✅ 从 CSV %s 加载 %s 个标的（股票 %s，只跟踪指数 %s）",
            table,
            len(result),
            stock_count,
            index_count,
        )
        return result

    return []


def fetch_trade_dates(start_date: str, end_date: str) -> list[dt.date]:
    last_error: Exception | None = None
    for attempt in range(BAOSTOCK_RETRY_ATTEMPTS):
        lg = bs.login()
        if lg.error_code != "0":
            last_error = RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        else:
            try:
                rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
                if rs.error_code != "0":
                    raise RuntimeError(f"交易日历查询失败: {rs.error_msg}")

                dates: list[dt.date] = []
                while rs.next():
                    row = rs.get_row_data()
                    if len(row) >= 2 and row[1] == "1":
                        dates.append(dt.datetime.strptime(row[0], "%Y-%m-%d").date())
                return dates
            except Exception as exc:
                last_error = exc
            finally:
                with suppress(Exception):
                    bs.logout()

        if attempt + 1 < BAOSTOCK_RETRY_ATTEMPTS:
            time.sleep(BAOSTOCK_RETRY_BASE_SLEEP * (2**attempt))

    if last_error is not None:
        raise last_error
    raise RuntimeError("交易日历查询失败，未知错误")


def latest_trade_date(lookback_days: int = 30) -> dt.date:
    end = dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    dates = fetch_trade_dates(start.isoformat(), end.isoformat())
    if not dates:
        raise RuntimeError("未查询到最近交易日")
    return dates[-1]


def load_symbols(
    logger: logging.Logger,
    *,
    as_of_date: str | None = None,
    include_indices: bool = True,
    limit: int | None = None,
    offset: int = 0,
) -> list[SymbolInfo]:
    local_result = _load_symbols_from_local_tables(
        logger,
        include_indices=include_indices,
        limit=limit,
        offset=offset,
    )
    if local_result:
        return local_result

    if as_of_date is None:
        as_of_date = latest_trade_date().isoformat()
    as_of = dt.datetime.strptime(as_of_date, "%Y-%m-%d").date()
    fallback_days = fetch_trade_dates((as_of - dt.timedelta(days=30)).isoformat(), as_of.isoformat())
    candidate_days = [as_of.isoformat()]
    candidate_days.extend(day.isoformat() for day in reversed(fallback_days) if day.isoformat() != as_of_date)

    last_error: Exception | None = None
    for attempt in range(BAOSTOCK_RETRY_ATTEMPTS):
        lg = bs.login()
        if lg.error_code != "0":
            last_error = RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        else:
            try:
                for candidate in candidate_days:
                    rs = bs.query_all_stock(day=candidate)
                    if rs.error_code != "0":
                        continue

                    result: list[SymbolInfo] = []
                    seen: set[str] = set()
                    while rs.next():
                        row = rs.get_row_data()
                        if len(row) < 3:
                            raise RuntimeError(f"股票列表返回异常行: {row}")
                        code = row[0]
                        name = row[2]

                        if "ST" in name or "退" in name:
                            continue

                        is_index = is_supported_index(code)
                        if not is_a_share(code) and not (include_indices and is_index):
                            continue
                        if code in seen:
                            continue

                        seen.add(code)
                        result.append(SymbolInfo(code, code, name, is_index))

                    if not result:
                        continue

                    result.sort(key=lambda item: (item.is_index, item.baostock_code))
                    if offset:
                        result = result[offset:]
                    if limit is not None:
                        result = result[:limit]

                    stock_count = sum(1 for item in result if not item.is_index)
                    index_count = len(result) - stock_count
                    logger.info(
                        "✅ 加载 %s 个标的（股票 %s，只跟踪指数 %s），股票列表日期 %s",
                        len(result),
                        stock_count,
                        index_count,
                        candidate,
                    )
                    return result
            except Exception as exc:
                last_error = exc
            finally:
                with suppress(Exception):
                    bs.logout()

        if attempt + 1 < BAOSTOCK_RETRY_ATTEMPTS:
            logger.warning("⚠ 获取股票清单失败，第%s次重试: %s", attempt + 1, last_error)
            time.sleep(BAOSTOCK_RETRY_BASE_SLEEP * (2**attempt))

    if last_error is not None:
        raise last_error
    logger.warning("⚠ 未获取到可用股票清单，返回空列表")
    return []


def split_date_ranges(dates: list[dt.date], span_days: int) -> list[tuple[dt.date, dt.date]]:
    if not dates:
        return []

    ranges: list[tuple[dt.date, dt.date]] = []
    start = dates[0]
    end = dates[0]
    for current in dates[1:]:
        if (current - start).days >= span_days:
            ranges.append((start, end))
            start = current
        end = current
    ranges.append((start, end))
    return ranges
