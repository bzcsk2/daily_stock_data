#!/usr/bin/env python3
"""Sync pytdx block membership snapshots into PostgreSQL."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json

import pandas as pd
import psycopg2
from kline_common import DEFAULT_DB_CONFIG, setup_logging
from psycopg2.extras import execute_values
from storage_common import append_upsert_csv, read_csv_table, use_csv, use_postgres
from tdx_common import code_to_symbol_guess, connect_hq_api

LOGGER = setup_logging("./logs/tdx_blocks.log")
SNAPSHOT_TABLE = "tdx_block_snapshots"
MEMBERSHIP_TABLE = "tdx_block_memberships"
BLOCK_FILES = ("block_gn.dat", "block_zs.dat", "block_fg.dat")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 pytdx 板块定义/成分快照")
    parser.add_argument("--block-files", nargs="*", default=list(BLOCK_FILES), help="要同步的 block 文件名")
    return parser.parse_args()


def get_conn():
    return psycopg2.connect(**DEFAULT_DB_CONFIG)


def ensure_schema() -> None:
    if not use_postgres():
        return

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
                    snapshot_id BIGSERIAL PRIMARY KEY,
                    block_file VARCHAR(32) NOT NULL,
                    content_hash VARCHAR(64) NOT NULL,
                    record_count INTEGER NOT NULL,
                    source VARCHAR(32) NOT NULL DEFAULT 'pytdx',
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (block_file, content_hash)
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {MEMBERSHIP_TABLE} (
                    snapshot_id BIGINT NOT NULL REFERENCES {SNAPSHOT_TABLE}(snapshot_id) ON DELETE CASCADE,
                    block_file VARCHAR(32) NOT NULL,
                    blockname VARCHAR(128) NOT NULL,
                    block_type INTEGER,
                    code_index INTEGER,
                    code VARCHAR(16) NOT NULL,
                    symbol_guess VARCHAR(16),
                    fetched_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (snapshot_id, blockname, code)
                )
                """
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{MEMBERSHIP_TABLE}_code ON {MEMBERSHIP_TABLE} (code)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{MEMBERSHIP_TABLE}_symbol_guess ON {MEMBERSHIP_TABLE} (symbol_guess)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{MEMBERSHIP_TABLE}_blockname ON {MEMBERSHIP_TABLE} (blockname)")
        LOGGER.info("✅ pytdx blocks 表结构检查完成")
    finally:
        conn.close()


def normalize_rows(block_file: str, rows: list[dict], fetched_at: dt.datetime) -> list[tuple]:
    normalized = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        code = str(row.get("code") or "").replace("\x00", "").strip()
        code = "".join(ch for ch in code if ch.isdigit())
        if len(code) != 6:
            continue
        blockname = str(row.get("blockname") or "").replace("\x00", "").strip()
        key = (blockname, code)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            (
                block_file,
                blockname,
                row.get("block_type"),
                row.get("code_index"),
                code,
                code_to_symbol_guess(code),
                fetched_at,
            )
        )
    normalized.sort(key=lambda item: (item[1], item[4], item[3] if item[3] is not None else -1))
    return normalized


def compute_hash(rows: list[tuple]) -> str:
    payload = [
        {
            "block_file": row[0],
            "blockname": row[1],
            "block_type": row[2],
            "code_index": row[3],
            "code": row[4],
            "symbol_guess": row[5],
        }
        for row in rows
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def get_existing_snapshot_info(block_file: str, content_hash: str) -> tuple[int, int] | None:
    if use_csv() and not use_postgres():
        snapshots = read_csv_table(SNAPSHOT_TABLE)
        if not snapshots.empty and {"snapshot_id", "block_file", "content_hash"}.issubset(snapshots.columns):
            matched = snapshots[(snapshots["block_file"] == block_file) & (snapshots["content_hash"] == content_hash)]
            if not matched.empty:
                snapshot_id = matched.iloc[-1]["snapshot_id"]
                memberships = read_csv_table(MEMBERSHIP_TABLE)
                member_count = 0
                if not memberships.empty and "snapshot_id" in memberships.columns:
                    member_count = int((memberships["snapshot_id"].astype(str) == str(snapshot_id)).sum())
                return snapshot_id, member_count

    if not use_postgres():
        return None

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT s.snapshot_id, COUNT(m.*) AS member_count
                FROM {SNAPSHOT_TABLE} s
                LEFT JOIN {MEMBERSHIP_TABLE} m ON m.snapshot_id = s.snapshot_id
                WHERE s.block_file = %s AND s.content_hash = %s
                GROUP BY s.snapshot_id
                """,
                (block_file, content_hash),
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else None
    finally:
        conn.close()


def upsert_snapshot(block_file: str, content_hash: str, record_count: int, fetched_at: dt.datetime) -> tuple[int, str]:
    existing = get_existing_snapshot_info(block_file, content_hash)
    if use_csv() and not use_postgres():
        csv_snapshot_id = int(content_hash[:16], 16)
        csv_status = "new"
        if existing is not None:
            _, member_count = existing
            csv_status = "incomplete" if member_count < record_count else "unchanged"
        append_upsert_csv(
            pd.DataFrame(
                [
                    {
                        "snapshot_id": csv_snapshot_id,
                        "block_file": block_file,
                        "content_hash": content_hash,
                        "record_count": record_count,
                        "source": "pytdx",
                        "first_seen_at": fetched_at,
                        "last_seen_at": fetched_at,
                    }
                ]
            ),
            SNAPSHOT_TABLE,
            ["block_file", "content_hash"],
        )
        if not use_postgres():
            return csv_snapshot_id, csv_status

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            if existing is not None:
                existing_id, member_count = existing
                cur.execute(
                    f"""
                    UPDATE {SNAPSHOT_TABLE}
                    SET last_seen_at = %s, record_count = %s
                    WHERE snapshot_id = %s
                    """,
                    (fetched_at, record_count, existing_id),
                )
                status = "incomplete" if member_count < record_count else "unchanged"
                snapshot_id = existing_id
                if use_csv():
                    append_upsert_csv(
                        pd.DataFrame(
                            [
                                {
                                    "snapshot_id": snapshot_id,
                                    "block_file": block_file,
                                    "content_hash": content_hash,
                                    "record_count": record_count,
                                    "source": "pytdx",
                                    "first_seen_at": fetched_at,
                                    "last_seen_at": fetched_at,
                                }
                            ]
                        ),
                        SNAPSHOT_TABLE,
                        ["block_file", "content_hash"],
                    )
                return snapshot_id, status

            cur.execute(
                f"""
                INSERT INTO {SNAPSHOT_TABLE} (
                    block_file, content_hash, record_count, source, first_seen_at, last_seen_at
                )
                VALUES (%s, %s, %s, 'pytdx', %s, %s)
                RETURNING snapshot_id
                """,
                (block_file, content_hash, record_count, fetched_at, fetched_at),
            )
            snapshot_id = cur.fetchone()[0]
            if use_csv():
                append_upsert_csv(
                    pd.DataFrame(
                        [
                            {
                                "snapshot_id": snapshot_id,
                                "block_file": block_file,
                                "content_hash": content_hash,
                                "record_count": record_count,
                                "source": "pytdx",
                                "first_seen_at": fetched_at,
                                "last_seen_at": fetched_at,
                            }
                        ]
                    ),
                    SNAPSHOT_TABLE,
                    ["block_file", "content_hash"],
                )
            return snapshot_id, "new"
    finally:
        conn.close()


def save_memberships(snapshot_id: int, rows: list[tuple]) -> int:
    if not rows:
        return 0
    values = [(snapshot_id, *row) for row in rows]
    if use_csv():
        columns = [
            "snapshot_id", "block_file", "blockname", "block_type", "code_index",
            "code", "symbol_guess", "fetched_at",
        ]
        written = append_upsert_csv(
            pd.DataFrame(values, columns=columns),
            MEMBERSHIP_TABLE,
            ["snapshot_id", "blockname", "code"],
        )
        if not use_postgres():
            return written

    sql = f"""
        INSERT INTO {MEMBERSHIP_TABLE} (
            snapshot_id, block_file, blockname, block_type, code_index, code, symbol_guess, fetched_at
        )
        VALUES %s
        ON CONFLICT (snapshot_id, blockname, code) DO UPDATE SET
            block_type = EXCLUDED.block_type,
            code_index = EXCLUDED.code_index,
            symbol_guess = EXCLUDED.symbol_guess,
            fetched_at = EXCLUDED.fetched_at
    """
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            execute_values(cur, sql, values, page_size=10000)
        return len(values)
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    ensure_schema()
    fetched_at = dt.datetime.now(dt.UTC)

    LOGGER.info("🚀 开始同步 pytdx blocks: %s", ", ".join(args.block_files))
    with connect_hq_api(LOGGER) as api:
        for block_file in args.block_files:
            raw_rows = api.get_and_parse_block_info(block_file)
            normalized_rows = normalize_rows(block_file, raw_rows, fetched_at)
            content_hash = compute_hash(normalized_rows)
            snapshot_id, status = upsert_snapshot(block_file, content_hash, len(normalized_rows), fetched_at)
            if status == "new":
                inserted = save_memberships(snapshot_id, normalized_rows)
                LOGGER.info(
                    "✅ %s 新快照已写入 snapshot_id=%s rows=%s hash=%s",
                    block_file,
                    snapshot_id,
                    inserted,
                    content_hash[:12],
                )
            elif status == "incomplete":
                inserted = save_memberships(snapshot_id, normalized_rows)
                LOGGER.info(
                    "♻ %s 快照已存在但成分未完整，已补写 snapshot_id=%s rows=%s hash=%s",
                    block_file,
                    snapshot_id,
                    inserted,
                    content_hash[:12],
                )
            else:
                LOGGER.info(
                    "ℹ %s 内容未变化，沿用 snapshot_id=%s hash=%s",
                    block_file,
                    snapshot_id,
                    content_hash[:12],
                )


if __name__ == "__main__":
    main()
