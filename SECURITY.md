# 安全策略

English summary: Do not publish credentials, private hosts, database dumps, or logs containing secrets.

## 支持版本

当前只支持 `main` 分支。

## 报告安全问题

如果公开 issue 会暴露密钥、私有基础设施或可利用细节，请使用 GitHub Security Advisory 或私下联系维护者。

不要在公开 issue 里粘贴 provider token、数据库密码、内部主机名或包含密钥的完整日志。

## 密钥处理

配置应通过环境变量或 `.env` 提供，`.env` 已被 Git 忽略。仓库只保留 `.env.example` 这种示例配置。
