#!/usr/bin/env python3
"""Export full pytdx F10 text files for all stocks."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from kline_common import setup_logging
from storage_common import data_dir
from tdx_common import TdxSymbol
from tdx_f10_common import connect_hq_api, ensure_export_dir, fetch_full_document, load_tdx_stock_universe

LOGGER = setup_logging("./logs/tdx_f10_export.log")
DEFAULT_EXPORT_DIR = str(data_dir() / "finance")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出所有股票的 pytdx F10 txt")
    parser.add_argument("--workers", type=int, default=4, help="并发 worker 数")
    parser.add_argument("--chunk-size", type=int, default=100, help="每个 worker 处理的股票数")
    parser.add_argument("--output-dir", default=DEFAULT_EXPORT_DIR, help="导出目录")
    parser.add_argument("--limit", type=int, default=None, help="仅导出前 N 只股票")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 只股票")
    return parser.parse_args()


def chunked(items: list[TdxSymbol], size: int) -> list[list[TdxSymbol]]:
    return [items[idx: idx + size] for idx in range(0, len(items), size)]


def export_chunk(symbols: list[TdxSymbol], output_dir: Path) -> tuple[int, list[str]]:
    written = 0
    exported: list[str] = []
    with connect_hq_api(LOGGER) as api:
        for symbol in symbols:
            filename, content = fetch_full_document(api, symbol)
            if not filename or not content:
                LOGGER.warning("⚠ 跳过空 F10 文档: %s %s filename=%s length=%s", symbol.symbol, symbol.name, filename, len(content))
                continue
            target = output_dir / f"{symbol.symbol}.txt"
            target.write_text(content, encoding="utf-8")
            exported.append(target.name)
            written += 1
    return written, exported


def cleanup_stale_files(output_dir: Path, exported_names: set[str]) -> int:
    deleted = 0
    for path in output_dir.glob("*.txt"):
        if path.name not in exported_names:
            path.unlink(missing_ok=True)
            deleted += 1
    return deleted


def main() -> None:
    args = parse_args()
    output_dir = ensure_export_dir(args.output_dir)
    symbols = load_tdx_stock_universe(LOGGER)
    if args.offset:
        symbols = symbols[args.offset:]
    if args.limit is not None:
        symbols = symbols[:args.limit]
    total_written = 0
    exported_names: set[str] = set()

    LOGGER.info(
        "🚀 开始导出 F10 txt，symbols=%s workers=%s output_dir=%s",
        len(symbols),
        args.workers,
        output_dir,
    )

    batches = chunked(symbols, args.chunk_size)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(export_chunk, batch, output_dir): idx for idx, batch in enumerate(batches, start=1)}
        for future in as_completed(futures):
            idx = futures[future]
            written, names = future.result()
            total_written += written
            exported_names.update(names)
            LOGGER.info("[batch %s/%s] written=%s total_written=%s", idx, len(batches), written, total_written)

    deleted = cleanup_stale_files(output_dir, exported_names)
    LOGGER.info("✅ F10 txt 导出完成，写入 %s 个文件，清理旧文件 %s 个", total_written, deleted)


if __name__ == "__main__":
    main()
