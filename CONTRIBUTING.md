# 贡献指南

English summary: Contributions are welcome when they keep the project runnable without private infrastructure.

欢迎贡献，但请保持项目不依赖任何个人机器路径、私有数据库或私有数据文件。

## 开发检查

提交 PR 前运行：

```bash
python -m py_compile scripts/*.py
for script in bin/run_*.sh; do bash -n "$script"; done
```

如果改动涉及存储行为，至少测试 `STORAGE_BACKEND=csv`。`STORAGE_BACKEND=postgres` 或 `both` 请只连接你自己的 PostgreSQL 实例。

## 数据和密钥

不要提交：

- `.env`
- provider tokens
- database credentials
- logs
- CSV output under `data/`
- database dumps
- exported F10 text corpora / 完整 F10 导出正文

配置项只写进 `.env.example`。

## 范围

采集脚本、存储代码和运行包装器都应避免本机绝对路径。新增任务应尽量支持统一存储模式：`csv`、`postgres`、`both`。
