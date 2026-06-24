#!/usr/bin/env python3
"""Shared helpers for pytdx F10 sync jobs."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from tdx_common import TdxSymbol, connect_hq_api, load_tdx_stock_universe

SECTION_TABLES = {
    "最新提示": "tdx_f10_latest_tip",
    "业内点评": "tdx_f10_commentary",
    "研究报告": "tdx_f10_research_report",
    "公司大事": "tdx_f10_corporate_event",
    "主力追踪": "tdx_f10_main_force_tracking",
    "龙虎榜单": "tdx_f10_dragon_tiger_list",
    "财务分析": "tdx_f10_financial_analysis",
    "股东研究": "tdx_f10_shareholder_research",
    "经营分析": "tdx_f10_business_analysis",
    "分红扩股": "tdx_f10_dividend_expansion",
    "公司概况": "tdx_f10_company_profile",
    "股本结构": "tdx_f10_equity_structure",
    "资本运作": "tdx_f10_capital_operation",
    "行业分析": "tdx_f10_industry_analysis",
    "高层治理": "tdx_f10_governance",
    "关联个股": "tdx_f10_related_stocks",
}

SECTION_GROUPS = {
    "daily": [
        "最新提示",
        "业内点评",
        "研究报告",
        "公司大事",
        "主力追踪",
        "龙虎榜单",
    ],
    "weekly": [
        "财务分析",
        "股东研究",
        "经营分析",
        "分红扩股",
    ],
    "biweekly": [
        "公司概况",
        "股本结构",
        "资本运作",
        "行业分析",
        "高层治理",
        "关联个股",
    ],
}

UPDATE_DATE_PATTERN = re.compile(r"更新日期[:：]\s*(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class F10SectionMeta:
    symbol: str
    stock_name: str
    section_name: str
    filename: str
    start_offset: int
    section_length: int


def normalize_text(text: str) -> str:
    return text.replace("\x00", "").strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_update_date(text: str) -> dt.date | None:
    match = UPDATE_DATE_PATTERN.search(text)
    if not match:
        return None
    return dt.datetime.strptime(match.group(1), "%Y-%m-%d").date()


def fetch_category_map(api, symbol: TdxSymbol) -> dict[str, F10SectionMeta]:
    df = api.to_df(api.get_company_info_category(symbol.market, symbol.code))
    result: dict[str, F10SectionMeta] = {}
    for _, row in df.iterrows():
        section_name = normalize_text(str(row["name"]))
        filename = normalize_text(str(row["filename"]))
        result[section_name] = F10SectionMeta(
            symbol=symbol.symbol,
            stock_name=symbol.name,
            section_name=section_name,
            filename=filename,
            start_offset=int(row["start"]),
            section_length=int(row["length"]),
        )
    return result


def fetch_text_range(api, symbol: TdxSymbol, filename: str, start: int, length: int) -> str:
    remaining = max(length, 0)
    offset = max(start, 0)
    chunks: list[str] = []

    while remaining > 0:
        chunk_len = min(20000, remaining)
        text = api.get_company_info_content(symbol.market, symbol.code, filename, offset, chunk_len)
        if not text:
            break
        cleaned = text.replace("\x00", "")
        chunks.append(cleaned)
        got = len(text)
        if got <= 0 or got < chunk_len:
            break
        offset += got
        remaining -= got

    return "".join(chunks).strip()


def fetch_section_texts(api, symbol: TdxSymbol, section_names: list[str]) -> dict[str, tuple[F10SectionMeta, str]]:
    categories = fetch_category_map(api, symbol)
    result: dict[str, tuple[F10SectionMeta, str]] = {}
    for section_name in section_names:
        meta = categories.get(section_name)
        if not meta:
            continue
        content = fetch_text_range(api, symbol, meta.filename, meta.start_offset, meta.section_length)
        result[section_name] = (meta, content)
    return result


def fetch_full_document(api, symbol: TdxSymbol) -> tuple[str, str]:
    categories = fetch_category_map(api, symbol)
    if not categories:
        return ("", "")
    metas = sorted(categories.values(), key=lambda item: item.start_offset)
    filename = metas[0].filename
    parts: list[str] = []
    for meta in metas:
        parts.append(fetch_text_range(api, symbol, meta.filename, meta.start_offset, meta.section_length))
    content = "\n\n".join(part for part in parts if part)
    return (filename, content)


def ensure_export_dir(path: str) -> Path:
    export_dir = Path(path)
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


__all__ = [
    "SECTION_GROUPS",
    "SECTION_TABLES",
    "F10SectionMeta",
    "connect_hq_api",
    "ensure_export_dir",
    "extract_update_date",
    "fetch_full_document",
    "fetch_section_texts",
    "load_tdx_stock_universe",
    "text_hash",
]
