# LokToken 本地 PostgreSQL 生产同构 Gate 报告

验证日期：2026-08-19  
验证环境：Windows Docker Desktop Linux Engine，Docker Compose v2.34.0  
项目名：`loktoken-gate`

## 结论

本地 PostgreSQL 生产同构环境和应用级 Gate 已通过，可以进入“购买一台新 ECS、部署预发布环境”阶段，但尚未满足直接接入真实用户的生产放行条件。

本次 Gate 证明了数据库迁移、应用启动、管理端/用户端业务闭环、账本一致性、容器重启持久化、备份恢复和页面联调可运行。真实模型调用、公网 TLS、外部安全通知、OIDC 和真实支付仍属于上线前外部依赖，不能用本地空密钥结果替代。

## 同构配置

- PostgreSQL：`postgres:17-alpine`，独立 Compose volume `loktoken-gate_token_postgres_data`
- 应用：生产配置，`TOKEN_ENVIRONMENT=production`
- 应用本地地址：`http://127.0.0.1:18000`
- PostgreSQL 本地地址：`127.0.0.1:15432`
- 资源上限：应用 1.25 CPU / 2 GiB，PostgreSQL 0.75 CPU / 1 GiB，总计模拟 2 CPU / 3 GiB；为新购 2C/4G ECS 预留系统余量
- `TOKEN_AUTO_CREATE_SCHEMA=false`
- `TOKEN_MOCK_MODE=false`
- `TOKEN_SEED_BUILTIN_MODELS=false`
- `TOKEN_LOKSYSTEM_SSO_ENABLED=false`
- OIDC 关闭，等待 LokSystem 身份中心的正式参数
- Gate 凭据只保存在被 Git 忽略的 `.env.gate`，没有写入报告或仓库

启动命令：

```powershell
docker compose --project-name loktoken-gate --env-file .env.gate `
  -f compose.yaml -f compose.gate.yaml up -d
```

## Gate 结果

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| Compose 配置解析 | 通过 | `docker compose ... config --quiet` |
| 空 PostgreSQL 迁移 | 通过 | 从空卷升级到 `0014_normalize_token_price_unit` |
| PostgreSQL 连接 | 通过 | `pg_isready` 返回 accepting connections |
| 应用健康检查 | 通过 | `/healthz` 和 `/readyz` 均 HTTP 200 |
| 自动化回归 | 通过 | `33 passed` |
| 管理员账号/角色/会话 | 通过 | superadmin、auditor、登出撤销均验证 |
| 用户注册/登录/团队空间/项目 | 通过 | 活体脚本验证 |
| 额度与账本 | 通过 | 入账幂等、兑换、账本对账 |
| API Key | 通过 | 创建、轮换、旧 Key 立即失效 |
| 订单 | 通过 | 待处理、确认、重复确认、退款、重复退款 |
| 请求记录与分析 | 通过 | PostgreSQL 有数据时 dashboard、hour/day 聚合均成功 |
| 容器冷重启 | 通过 | 重启后健康，迁移版本和关键数据保留 |
| PostgreSQL 备份恢复 | 通过 | `pg_dump` 恢复到独立数据库，关键表计数一致 |
| 并发健康冒烟 | 通过 | 100 个并发 `/readyz` 请求，0 失败，约 0.914 秒 |
| 管理端页面 UAT | 通过 | 8 个侧栏入口逐项打开，0 个浏览器 error/warning |
| 用户中心页面 UAT | 通过 | 7 个侧栏入口逐项打开，0 个浏览器 error/warning |
| Logo/标题 | 通过 | favicon `/static/loksystem-icon.png`，两个页面标题正确 |
| 容器资源 | 通过 | 应用约 70 MiB，PostgreSQL 约 57 MiB |

## 发现并修复的问题

### 1. PostgreSQL Alembic 版本号长度不足

Alembic 默认生成的 `alembic_version.version_num` 为 `VARCHAR(32)`，项目的描述性 revision ID 超过 32 字符，SQLite 不会暴露该问题，PostgreSQL 会在迁移到 `0006` 时失败。

已在 [`migrations/env.py`](../migrations/env.py) 对 PostgreSQL 做启动引导，将版本号列建为/扩展为 `VARCHAR(255)`。空库迁移和重复迁移均已通过。

### 2. PostgreSQL 不支持 SQLite 的时间分桶函数

用量分析原先使用 `strftime()`，在 PostgreSQL 会报错；dashboard 的日期聚合也需要区分数据库类型。

已在 [`app/portal.py`](../app/portal.py) 对 PostgreSQL 使用 `date_trunc` / `CAST(... AS DATE)`，SQLite 保留原实现。两种数据库的自动化测试和 PostgreSQL 有数据聚合验证均通过。

### 3. 活体验证脚本

新增 [`scripts/gate_live.py`](../scripts/gate_live.py)，覆盖健康检查、RBAC、团队、额度、兑换、Key 轮换、订单状态、对账、分析和会话撤销。脚本只从环境变量读取 Gate 管理令牌，不打印供应商密钥。

## 真实模型验证状态

真实 DeepSeek V4 Flash 已在当前 `8000` SQLite 服务完成闭环验证，详见 [`REAL_DEEPSEEK_GATE_REPORT.md`](REAL_DEEPSEEK_GATE_REPORT.md)。本地 PostgreSQL Gate 使用独立 `.env.gate`，没有复制供应商密钥，因此 PostgreSQL 容器中的 DeepSeek 模型仍保持阻断状态，这是有意的环境隔离。

已通过：真实 chat、真实 stream、供应商 usage 读取、平台计费、余额不足、重复请求、普通失败退款和流式失败退款。

仍未通过或未完成：实际 DeepSeek 账单原始流水逐笔导入、多渠道真实重试/熔断接管、Qwen/GLM/Kimi/MiniMax/Doubao 真实调用。配置这些真实密钥后，必须在预发布环境逐个执行 `POST /admin/models/{id}/preflight` 和真实模型 UAT，不能把单一 DeepSeek 的结果扩展为全供应商通过。

## 上线前阻断项

1. 购买 ECS 前可进入下一阶段，但购买后必须先部署预发布，不应直接切生产。
2. 建立 `token.lokai.net.cn` DNS、TLS 证书和旧 ECS Nginx 到新 ECS 私网地址的反向代理。
3. 配置真实供应商密钥，并只通过管理端渠道配置或正式密钥管理注入；完成 chat、stream、usage、retry、circuit breaker 验证。
4. 准备 HTTPS 安全通知 Webhook 接收端并做密码重置、Key 轮换、会话撤销通知 UAT。
5. 决定 LokSystem 统一账号方案并配置正式 OIDC issuer、client、回调地址；当前本地 OIDC 仅有自动化模拟验证。
6. 真实支付仍未接入，本 Gate 只验证人工确认、退款和对账状态机；支付上线前需单独做沙箱验签、回调幂等和对账 UAT。
7. 在预发布环境用真实域名完成管理员、独立用户、LokSystem 跳转用户、企业团队和渠道分发五类完整 UAT，全部通过后再切 DNS。

## 建议的下一步

1. 按已确认的新购方案购买 2C/4G、40 GiB ECS，一个月先行。
2. 先以预发布模式部署 PostgreSQL、Token 服务和 Nginx，导入本地 Gate 使用的迁移版本，不导入 Gate 测试账号和测试业务数据。
3. 配置首批真实供应商密钥和 `token.lokai.net.cn`，完成真实模型预检与完整 UAT。
4. 真实模型和安全通知全部通过后，再执行生产切换、备份策略和监控告警验收。
