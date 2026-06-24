#!/usr/bin/env python3
"""Storage helpers for CSV and PostgreSQL backends."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parent


def load_project_env(path: str | None = None) -> None:
    env_path = Path(path) if path else PROJECT_DIR / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


load_project_env()


def storage_backend() -> str:
    backend = os.getenv("STORAGE_BACKEND", "csv").strip().lower()
    aliases = {
        "pg": "postgres",
        "postgresql": "postgres",
        "db": "postgres",
    }
    backend = aliases.get(backend, backend)
    if backend not in {"csv", "postgres", "both"}:
        raise ValueError("STORAGE_BACKEND must be one of: csv, postgres, both")
    return backend


def use_csv() -> bool:
    return storage_backend() in {"csv", "both"}


def use_postgres() -> bool:
    return storage_backend() in {"postgres", "both"}


def data_dir() -> Path:
    path = Path(os.getenv("DATA_DIR", PROJECT_DIR / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def csv_path(table: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", table).strip("_")
    return data_dir() / f"{safe_name}.csv"


def read_csv_table(table: str, parse_dates: Iterable[str] | None = None) -> pd.DataFrame:
    path = csv_path(table)
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=list(parse_dates or []))


def write_csv_table(df: pd.DataFrame, table: str) -> int:
    if df.empty:
        return 0
    path = csv_path(table)
    df.to_csv(path, index=False)
    return len(df)


def append_upsert_csv(
    df: pd.DataFrame,
    table: str,
    key_columns: list[str],
    *,
    parse_dates: Iterable[str] | None = None,
) -> int:
    if df.empty:
        return 0

    date_columns = list(parse_dates or [])
    incoming = df.copy()
    for column in date_columns:
        if column in incoming.columns:
            incoming[column] = pd.to_datetime(incoming[column], errors="coerce")

    existing = read_csv_table(table, parse_dates=parse_dates)
    merged = pd.concat([existing, incoming], ignore_index=True) if not existing.empty else incoming
    if key_columns:
        merged = merged.drop_duplicates(subset=key_columns, keep="last")
        merged = merged.sort_values(key_columns)
    write_csv_table(merged, table)
    return len(df)


def replace_csv_slice(
    df: pd.DataFrame,
    table: str,
    key_columns: list[str],
    *,
    symbol: str,
    start_date,
    end_date,
    time_column: str = "time",
) -> int:
    incoming = df.copy()
    if not incoming.empty and time_column in incoming.columns:
        incoming[time_column] = pd.to_datetime(incoming[time_column], errors="coerce")

    existing = read_csv_table(table, parse_dates=[time_column])
    if not existing.empty and time_column in existing.columns and "symbol" in existing.columns:
        times = pd.to_datetime(existing[time_column], errors="coerce")
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date) + pd.Timedelta(days=1)
        mask = (existing["symbol"] == symbol) & (times >= start) & (times < end)
        existing = existing[~mask]

    merged = pd.concat([existing, incoming], ignore_index=True) if not incoming.empty else existing
    if not merged.empty and key_columns:
        merged = merged.drop_duplicates(subset=key_columns, keep="last").sort_values(key_columns)
    write_csv_table(merged, table)
    return len(incoming)
