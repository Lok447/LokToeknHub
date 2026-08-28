# 生产发布闭环 Runbook

本文用于预发布和生产发布，不替代支付机构、供应商或变更审批流程。

## 1. 发布前门禁

在部署主机执行：

```bash
set -a; source .env.docker; set +a
uv run python scripts/production_preflight.py
```

`FAILED` 表示基础配置不能启动生产服务；`BLOCKED` 表示基础配置通过但仍缺少真实支付凭证或安全通知配置，不能对外售卖。

应用启动前必须满足：`TOKEN_ENVIRONMENT=production`、关闭 Mock 和自动建表、PostgreSQL、Redis、HTTPS 公网地址、独立随机密钥，以及 `TOKEN_REQUIRE_REAL_PAYMENT=true`。

## 2. 数据库备份与恢复演练

备份文件放在独立磁盘或对象存储，不与应用数据卷共用：

```bash
TOKEN_DATABASE_URL="$TOKEN_DATABASE_URL" TOKEN_BACKUP_DIR=/srv/backups/loktoken \
  bash scripts/backup_postgres.sh
```

恢复演练必须使用隔离数据库，并显式确认：

```bash
CONFIRM_RESTORE=YES TOKEN_DATABASE_URL="postgresql+psycopg://.../loktoken_restore" \
  bash scripts/restore_postgres.sh /srv/backups/loktoken/loktoken-<timestamp>.dump
```

恢复后执行 `alembic current`、`/readyz`、账本对账和一笔不扣费的健康检查。生产库禁止直接降级迁移；回滚应用版本前先验证新版本是否兼容当前数据库 revision。

## 3. 真实供应商验收

每家供应商至少完成一次真实 `/models` 同步、普通调用、流式调用、Token/成本核验、429/5xx/超时失败，以及主备渠道切换。将请求 ID、供应商请求 ID、账单导入结果和毛利报表保存到发布记录。未完成真实凭证验证的模型保持候选状态。

## 4. 真实支付验收

当前支付抽象仍以人工确认作为开发/联调渠道。正式售卖前必须完成至少一个已实现的真实支付适配器，验证支付链接或二维码、异步回调验签、重复回调幂等、金额一致性、退款审批和财务对账。仅配置商户环境变量但适配器未实现时，`/admin/runtime` 的 `real_payment_ready` 仍为 `false`，发布门禁不会放行。

## 5. 发布后检查

```bash
docker compose -p loktoken --env-file .env.docker ps
curl -fsS https://token.example.com/healthz
curl -fsS https://token.example.com/readyz
docker compose -p loktoken --env-file .env.docker logs --tail=200 token
```

确认 `main` 提交、Alembic revision、镜像摘要、备份文件校验和发布负责人均已记录。出现扣费、支付或数据异常时，保留 Request ID、订单号、供应商请求 ID 和账本流水，先停止放量再执行回滚预案。
