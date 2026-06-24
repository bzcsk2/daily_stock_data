# 运维说明

English summary: Start with CSV storage, install only the cron jobs you need, and keep generated data/logs outside Git.

## 推荐启动方式

先使用 CSV 模式：

```bash
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./bin/run_daily_sync_batches.sh
```

当你需要并发查询、更大数据量或跨表 SQL 分析时，再切换到 PostgreSQL。

## 定时任务

使用 `cron.example` 作为模板，只安装你需要的任务。逐笔成交和 F10 导出都可能产生较大数据量，应谨慎设置频率和保留策略。

## 日志和数据

- 日志：`logs/`
- CSV 输出：`DATA_DIR`，默认 `./data`
- F10 文本导出：默认 `DATA_DIR/finance`

这些路径都被 `.gitignore` 排除。

## 维护检查

```bash
python -m py_compile scripts/*.py
for script in bin/run_*.sh; do bash -n "$script"; done
```
