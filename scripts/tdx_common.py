#!/usr/bin/env python3
"""Common helpers for pytdx-based reference data sync jobs."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager, suppress
from dataclasses import dataclass

from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams

from kline_common import latest_trade_date, load_symbols

DEFAULT_HOST_CANDIDATES: list[tuple[str, int, str]] = [
    ("180.153.18.170", 7709, "上海电信主站Z1"),
    ("180.153.18.171", 7709, "上海电信主站Z2"),
    ("202.108.253.130", 7709, "北京联通主站Z1"),
    ("202.108.253.131", 7709, "北京联通主站Z2"),
    ("123.125.108.14", 7709, "上证云北京联通一"),
]


def _host_candidates_from_env() -> list[tuple[str, int, str]]:
    raw_value = os.getenv("TDX_HOSTS", "").strip()
    if not raw_value:
        return DEFAULT_HOST_CANDIDATES

    result: list[tuple[str, int, str]] = []
    for raw_item in raw_value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        parts = item.split(":", 2)
        if len(parts) < 2:
            continue
        host = parts[0].strip()
        try:
            port = int(parts[1])
        except ValueError:
            continue
        alias = parts[2].strip() if len(parts) == 3 and parts[2].strip() else host
        result.append((host, port, alias))

    return result or DEFAULT_HOST_CANDIDATES


HOST_CANDIDATES = _host_candidates_from_env()


@dataclass(frozen=True)
class TdxSymbol:
    symbol: str
    market: int
    code: str
    name: str


def symbol_to_tdx_parts(symbol: str) -> tuple[int, str] | None:
    normalized = symbol.strip().lower()
    if normalized.startswith("sh."):
        return (TDXParams.MARKET_SH, normalized[3:])
    if normalized.startswith("sz."):
        return (TDXParams.MARKET_SZ, normalized[3:])
    return None


def code_to_symbol_guess(code: str) -> str | None:
    normalized = str(code).strip()
    if len(normalized) != 6 or not normalized.isdigit():
        return None
    if normalized.startswith(("6", "9")):
        return f"sh.{normalized}"
    if normalized.startswith(("0", "1", "2", "3")):
        return f"sz.{normalized}"
    return None


def load_tdx_stock_universe(logger: logging.Logger) -> list[TdxSymbol]:
    as_of_date = latest_trade_date().isoformat()
    symbols = load_symbols(logger, as_of_date=as_of_date, include_indices=False)
    result: list[TdxSymbol] = []
    seen: set[str] = set()
    for item in symbols:
        parts = symbol_to_tdx_parts(item.db_symbol)
        if not parts:
            continue
        market, code = parts
        if item.db_symbol in seen:
            continue
        seen.add(item.db_symbol)
        result.append(TdxSymbol(item.db_symbol, market, code, item.name))
    result.sort(key=lambda item: item.symbol)
    return result


@contextmanager
def connect_hq_api(logger: logging.Logger, *, heartbeat: bool = True, timeout: int = 5):
    last_error: Exception | None = None
    for ip, port, alias in HOST_CANDIDATES:
        api = TdxHq_API(heartbeat=heartbeat)
        try:
            ok = api.connect(ip, port, time_out=timeout)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("⚠ 连接 pytdx 主机失败 %s (%s:%s): %s", alias, ip, port, exc)
            with suppress(Exception):
                api.disconnect()
            continue

        if not ok:
            with suppress(Exception):
                api.disconnect()
            continue

        logger.info("✅ 已连接 pytdx 主机 %s (%s:%s)", alias, ip, port)
        try:
            yield api
        finally:
            with suppress(Exception):
                api.disconnect()
        return

    if last_error is not None:
        raise RuntimeError(f"无法连接任何 pytdx 行情主机: {last_error}")
    raise RuntimeError("无法连接任何 pytdx 行情主机")
