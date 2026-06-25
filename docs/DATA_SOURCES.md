# 数据源说明 / Data sources

English summary: This document explains what each upstream provider is used for, which credentials are optional, and where users should pay attention to rate limits and data semantics.

## 数据源矩阵

| 数据源 | 用途 | 凭证 | 备注 |
| --- | --- | --- | --- |
| baostock | 交易日历、股票清单、指数日线、日线/5 分钟兜底 | 不需要 | 网络可用性依赖公开服务 |
| Tushare | 股票日线优先源、`stock_basic` | `TUSHARE_TOKEN` | 额度和接口权限由 Tushare 账号决定 |
| TickFlow | 股票日线补充源、instruments 基础资料 | `TICKFLOW_API_KEY` | `TICKFLOW_BASE_URL` 可配置 |
| Tencent/easyquotation | 交易时段行情快照 | 不需要项目级密钥 | 适合实时快照，不适合替代正式行情授权 |
| pytdx | 除权除息、财务快照、板块、逐笔、F10 | 不需要项目级密钥 | 主机可用性会变化，可通过 `TDX_HOSTS` 覆盖 |

## 日线 provider 顺序

股票日线采集顺序为：Tushare -> TickFlow -> baostock。指数日线使用 baostock。若上游失败，脚本会尝试下一个 provider。

当前表结构尚未在每行记录 `source`，因此出现跨 provider 降级时，需要通过运行日志追溯数据来源。后续建议在日线和分钟线表增加 `source`、`fetched_at`、`adjust_flag` 等元数据列。

## 复权语义

baostock K 线请求当前使用 `adjustflag="3"`。用户在把数据接入回测或分析系统前，应先确认该口径是否满足策略需求，并在下游显式记录复权假设。

## 频率限制和合规

本项目不绕过上游限制，也不提供任何行情授权。使用前请自行确认：

- 上游服务条款；
- 账号额度、频率限制和并发限制；
- 数据是否允许再分发；
- 生产环境是否需要正式商业行情授权。

## 推荐使用方式

- 初次试用：CSV + 日线 + stock_basic。
- 长期运行：PostgreSQL + cron + 日志轮转。
- 高频快照/逐笔/F10：优先 PostgreSQL，并设置清晰的数据保留策略。
