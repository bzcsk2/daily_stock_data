# ruff: noqa: E402,I001
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import storage_common  # noqa: E402


def _configure_csv(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "csv")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def test_append_upsert_csv_replaces_existing_key(monkeypatch, tmp_path: Path) -> None:
    _configure_csv(monkeypatch, tmp_path)

    first = pd.DataFrame(
        [
            {"time": "2026-01-02", "symbol": "sh.600000", "close": 10.0},
            {"time": "2026-01-03", "symbol": "sh.600000", "close": 11.0},
        ]
    )
    second = pd.DataFrame(
        [
            {"time": "2026-01-03", "symbol": "sh.600000", "close": 12.5},
            {"time": "2026-01-04", "symbol": "sh.600000", "close": 13.0},
        ]
    )

    assert storage_common.append_upsert_csv(first, "daily_ohlcv", ["time", "symbol"], parse_dates=["time"]) == 2
    assert storage_common.append_upsert_csv(second, "daily_ohlcv", ["time", "symbol"], parse_dates=["time"]) == 2

    result = storage_common.read_csv_table("daily_ohlcv", parse_dates=["time"])
    assert len(result) == 3
    latest = result[result["time"] == pd.Timestamp("2026-01-03")].iloc[0]
    assert latest["close"] == 12.5


def test_replace_csv_slice_only_replaces_target_symbol_window(monkeypatch, tmp_path: Path) -> None:
    _configure_csv(monkeypatch, tmp_path)

    existing = pd.DataFrame(
        [
            {"time": "2026-01-02 09:35:00", "symbol": "sh.600000", "close": 10.0},
            {"time": "2026-01-03 09:35:00", "symbol": "sh.600000", "close": 11.0},
            {"time": "2026-01-03 09:35:00", "symbol": "sz.000001", "close": 20.0},
            {"time": "2026-01-05 09:35:00", "symbol": "sh.600000", "close": 14.0},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"time": "2026-01-03 09:40:00", "symbol": "sh.600000", "close": 12.0},
            {"time": "2026-01-04 09:35:00", "symbol": "sh.600000", "close": 13.0},
        ]
    )

    storage_common.write_csv_table(existing, "min5_ohlcv")
    assert (
        storage_common.replace_csv_slice(
            incoming,
            "min5_ohlcv",
            ["time", "symbol"],
            symbol="sh.600000",
            start_date="2026-01-03",
            end_date="2026-01-04",
        )
        == 2
    )

    result = storage_common.read_csv_table("min5_ohlcv", parse_dates=["time"])
    assert len(result) == 5
    assert set(result[result["symbol"] == "sh.600000"]["close"]) == {10.0, 12.0, 13.0, 14.0}
    assert result[result["symbol"] == "sz.000001"].iloc[0]["close"] == 20.0
