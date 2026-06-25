#!/usr/bin/env python3
"""Backfill pytdx tick-by-tick trades month by month, newest to oldest."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from kline_common import fetch_trade_dates, setup_logging
from sync_tdx_tick_trades import PAGE_SIZE_DEFAULT, SESSION_PM, parse_trade_date, run_sync
from tdx_common import load_tdx_stock_universe

LOGGER = setup_logging("./logs/tdx_tick_trades_backfill.log")
DEFAULT_PROGRESS_FILE = "./logs/tdx_tick_trades_backfill_progress.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按月向过去回填 pytdx 逐笔成交")
    parser.add_argument("--start-date", default="2026-04-03", help="回填起点，格式 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--end-date", default="2024-07-01", help="回填终点，格式 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--workers", type=int, default=4, help="并发 worker 数")
    parser.add_argument("--chunk-size", type=int, default=25, help="每个 worker 处理的 symbol 数")
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE_DEFAULT, help="逐笔接口分页大小")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 个标的，便于测试")
    parser.add_argument("--offset", type=int, default=0, help="标的偏移，便于测试")
    parser.add_argument("--progress-file", default=DEFAULT_PROGRESS_FILE, help="回填进度文件路径")
    parser.add_argument("--max-months", type=int, default=None, help="本次最多处理多少个月，便于分批回填")
    parser.add_argument("--max-trade-days", type=int, default=None, help="本次最多处理多少个交易日，便于测试")
    return parser.parse_args()


def month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def prev_month(day: dt.date) -> dt.date:
    first = month_start(day)
    return first - dt.timedelta(days=1)


def iter_months_desc(start: dt.date, end: dt.date) -> list[dt.date]:
    months: list[dt.date] = []
    current = month_start(start)
    end_month = month_start(end)
    while current >= end_month:
        months.append(current)
        current = month_start(prev_month(current))
    return months


def month_end(day: dt.date) -> dt.date:
    next_candidate = day.replace(day=28) + dt.timedelta(days=4)
    return next_candidate.replace(day=1) - dt.timedelta(days=1)


def load_progress(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_progress(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def normalize_progress(progress: dict, start_date: dt.date, end_date: dt.date) -> dict:
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "completed_months": progress.get("completed_months", []),
        "completed_trade_dates": progress.get("completed_trade_dates", {}),
        "last_completed_trade_date": progress.get("last_completed_trade_date"),
        "updated_at": progress.get("updated_at"),
    }


def main() -> None:
    args = parse_args()
    start_date = parse_trade_date(args.start_date)
    end_date = parse_trade_date(args.end_date)
    if start_date < end_date:
        raise SystemExit("start-date 必须晚于或等于 end-date")

    progress_path = Path(args.progress_file)
    progress = normalize_progress(load_progress(progress_path), start_date, end_date)

    universe = load_tdx_stock_universe(LOGGER)
    if args.offset:
        universe = universe[args.offset:]
    if args.limit is not None:
        universe = universe[:args.limit]

    processed_trade_days = 0
    processed_months = 0
    for month_day in iter_months_desc(start_date, end_date):
        month_key = month_day.strftime("%Y-%m")
        if month_key in progress["completed_months"]:
            LOGGER.info("⏭ 月份 %s 已完成，跳过", month_key)
            continue
        if args.max_months is not None and processed_months >= args.max_months:
            LOGGER.info("⏹ 达到 max-months=%s，本次停止", args.max_months)
            break

        range_start = max(month_day, end_date)
        range_end = min(month_end(month_day), start_date)
        trade_dates = fetch_trade_dates(range_start.isoformat(), range_end.isoformat())
        trade_dates = [item for item in trade_dates if end_date <= item <= start_date]
        trade_dates.sort(reverse=True)

        completed_for_month = set(progress["completed_trade_dates"].get(month_key, []))
        LOGGER.info(
            "📦 开始回填月份 %s，trade_dates=%s，已完成=%s",
            month_key,
            len(trade_dates),
            len(completed_for_month),
        )

        for trade_date in trade_dates:
            if args.max_trade_days is not None and processed_trade_days >= args.max_trade_days:
                LOGGER.info("⏹ 达到 max-trade-days=%s，本次停止", args.max_trade_days)
                save_progress(progress_path, progress)
                return

            trade_date_key = trade_date.isoformat()
            if trade_date_key in completed_for_month:
                LOGGER.info("⏭ %s 已完成，跳过", trade_date_key)
                continue

            LOGGER.info("🗓 开始回填 %s", trade_date_key)
            stats = run_sync(
                trade_date=trade_date,
                use_history=True,
                session_name=SESSION_PM,
                backfill_am=True,
                workers=args.workers,
                chunk_size=args.chunk_size,
                page_size=args.page_size,
                symbols=universe,
            )
            completed_for_month.add(trade_date_key)
            progress["completed_trade_dates"][month_key] = sorted(completed_for_month)
            progress["last_completed_trade_date"] = trade_date_key
            progress["updated_at"] = dt.datetime.now(dt.UTC).isoformat()
            save_progress(progress_path, progress)
            processed_trade_days += 1
            LOGGER.info("✅ 完成 %s stats=%s", trade_date_key, stats)

        progress["completed_months"] = sorted(set(progress["completed_months"]) | {month_key}, reverse=True)
        progress["updated_at"] = dt.datetime.now(dt.UTC).isoformat()
        save_progress(progress_path, progress)
        processed_months += 1
        LOGGER.info("✅ 月份 %s 回填完成", month_key)

    LOGGER.info(
        "🏁 pytdx 逐笔历史回填完成，months=%s trade_days=%s progress=%s",
        processed_months,
        processed_trade_days,
        progress_path,
    )


if __name__ == "__main__":
    main()
