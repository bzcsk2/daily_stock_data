# 数据契约 / Data contracts

English summary: This document defines the expected CSV/PostgreSQL table contracts, key columns, time semantics, and known scale limits.

## 通用约定

- 默认市场时区：`Asia/Shanghai`。
- CSV 文件位于 `DATA_DIR`，默认 `./data`。
- CSV 写入是本地文件写入，不提供跨进程事务；长期高频采集建议使用 PostgreSQL。
- PostgreSQL 写入通常使用 `ON CONFLICT` 或区间删除后重写。
- 行情数据仅用于研究和工程测试，不构成投资建议。

## `daily_ohlcv`

股票日线表。主键/去重键：`time, symbol`。

| 列 | 含义 |
| --- | --- |
| `time` | 交易日，日线粒度 |
| `symbol` | baostock 风格代码，例如 `sh.600000` |
| `open/high/low/close` | 开高低收 |
| `volume` | 成交量，按上游标准化后的数值 |
| `turnover` | 成交额，采集器尽量归一到元级口径 |
| `turn` | 换手率，可能为空 |
| `pe_ttm/pb_mrq/ps_ttm/pcf_ncf_ttm` | 估值字段，可能为空 |
| `is_st` | 是否 ST，可能来自 baostock 字段 |

## `index_daily`

指数日线表。主键/去重键：`time, symbol`。

| 列 | 含义 |
| --- | --- |
| `time` | 交易日 |
| `symbol` | 指数代码，例如 `sh.000001` |
| `open/high/low/close` | 开高低收 |
| `volume` | 成交量 |
| `turnover` | 成交额 |

## `min5_ohlcv` / `index_min5`

5 分钟 K 线表。主键/去重键：`time, symbol`。

| 列 | 含义 |
| --- | --- |
| `time` | 5 分钟 bar 结束时间，市场时区语义 |
| `symbol` | 股票或指数代码 |
| `open/high/low/close` | 开高低收 |
| `volume` | 成交量 |
| `turnover` | 成交额 |
| `amount` | 兼容旧字段，当前等于 `turnover` |

采集器会过滤交易时段外异常 bar。区间修复时会先删除目标 `symbol` 在日期窗口内的旧数据，再写入新数据。

## `quote_snapshots_unified`

腾讯行情快照统一表。主键/去重键：`snapshot_time, source, symbol`。

| 列 | 含义 |
| --- | --- |
| `source` | 数据源，当前为 `tencent` |
| `source_snapshot_id` | 上游快照 ID，当前可能为空 |
| `symbol` | 股票代码 |
| `name` | 股票名称 |
| `snapshot_time` | 采集时间 |
| `price/open/close/high/low` | 当前价和 OHLC 字段 |
| `buy/sell` | 买卖参考价 |
| `amount` | 当前采集器保留的金额/成交相关字段 |
| `volume_shares/volume_lots` | 成交量字段，按腾讯返回字段派生 |
| `change_amount/change_percent/amplitude` | 涨跌、涨跌幅、振幅 |
| `market_cap/circulating_cap` | 总市值、流通市值 |
| `pe/pb` | 估值字段 |
| `limit_up_price/limit_down_price` | 涨停价、跌停价 |
| `is_limit_up/is_limit_down` | 是否触及涨跌停 |
| `bid*/ask*` | 五档报价和量 |
| `raw_volume/raw_turnover/raw_amount_wan` | 上游原始成交字段留痕 |

## `latest_tick`

当前最新快照表。主键：`symbol`。CSV 模式每轮会重写该表，PostgreSQL 模式在表存在时 upsert。

## `stock_basic_tushare`

Tushare 股票基础资料表。主键：`ts_code`。

| 列 | 含义 |
| --- | --- |
| `ts_code` | Tushare 代码，例如 `600000.SH` |
| `symbol` | 六位股票代码 |
| `name` | 股票名称 |
| `area/industry/fullname/enname/cnspell` | 基础资料字段 |
| `market/exchange/curr_type/list_status` | 市场、交易所、币种、上市状态 |
| `list_date/delist_date` | 上市/退市日期 |
| `is_hs/act_name/act_ent_type` | 沪深港通和实控人相关字段 |
| `source/fetched_at/updated_at` | 数据源和采集时间 |

## CSV 规模边界

CSV 模式为了开箱即用，采用本地文件读写。日线和基础资料适合 CSV；5 分钟、逐笔、F10 和高频快照长期运行建议 PostgreSQL，或后续迁移到按日期/月分片的 CSV/Parquet/DuckDB 后端。
