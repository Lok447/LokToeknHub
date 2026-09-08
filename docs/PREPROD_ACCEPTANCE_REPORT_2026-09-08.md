# LokToken 预生产验收报告

## 1. 结论

本次隔离预生产验收结论为：**条件通过，不批准直接商业化上线**。

核心网关能力在本地双实例、PostgreSQL、Redis 和真实 HTTP 模拟供应商条件下通过；生产发布前仍必须完成真实供应商 SDK、企业 IdP/SCIM、支付商户、电子发票和 OpenTelemetry 平台的外部联调与演练。

## 2. 环境与范围

- Compose 项目：`loktoken-preprod`
- 应用实例：`127.0.0.1:18000`、`127.0.0.1:18001`
- PostgreSQL 17、Redis 7
- 模拟供应商：宿主机 `127.0.0.1:4010`，容器通过 `host.docker.internal`
- 迁移头：`0033_schema_completeness (head)`
- 运行模式：`TOKEN_ENVIRONMENT=staging`、`TOKEN_MOCK_MODE=false`、真实 Redis/PostgreSQL

## 3. 通过项

验收脚本 `runtime-logs/preprod-acceptance.json` 的最新运行结果为 `passed=true`，覆盖：

- 两实例 `/healthz`、`/readyz`
- 管理员 bootstrap/login、账户、API Key、充值和预算拒绝
- 真实 HTTP 供应商健康检查及模型发布门禁
- OpenAI Chat HTTP/SSE
- OpenAI Responses、Anthropic、Gemini 协议转换
- Redis 跨实例响应缓存和 API Key 限流
- Trace 路由尝试、失败分类字段和 Prometheus 指标
- 支付 Webhook 签名、重复投递幂等

负向场景中停止模拟供应商后，健康检查将模型标记为 `unhealthy`、发布状态为 `blocked`，请求返回 503；恢复供应商后再次通过，证明发布门禁和恢复路径有效。

## 4. 本轮修复

1. 修复空 PostgreSQL 部署中 `generation_tasks` 缺少建表迁移的问题，完善 `0030_gateway_reliability`。
2. 新增 `0033_schema_completeness`，补齐 ORM 与生产迁移之间遗漏的字段、索引和 `model_change_records` 表。
3. `/readyz` 增加关键表字段检查，迁移不完整时返回 503。
4. 双实例 Compose 拆出一次性 `migrate` 服务，应用实例不再并发执行 Alembic。
5. 基础 Compose 补齐缓存、并发、Worker 配置，确保多实例配置一致。
6. 预生产脚本改为可重复执行，认证、账户和模型失败时安全停止并写报告。

## 5. 上线前待办

- 使用每个实际供应商的 sandbox/官方 SDK 完成模型、流式、超时、429、5xx、计费和退款 Golden Test。
- 接入真实企业 IdP，验证 OIDC 登录、SCIM 创建/更新/停用、组和权限映射，完成密钥轮换演练。
- 接入真实支付商户和电子发票服务，验证签名、公钥轮换、退款、对账、发票开具和重试。
- 部署 OpenTelemetry Collector、指标长期存储、日志脱敏、告警通知和 SLO 看板，并进行故障演练。
- 进行 Redis/PostgreSQL 高可用、备份恢复、跨可用区和滚动升级演练。
- 对异步图像/音频/视频任务制造失败任务，验证重试、租约抢占、死信、人工 replay、退款和审计闭环。
- 使用正式域名、TLS、WAF、密钥管理系统和生产级安全扫描结果替换本地 UAT 配置。

## 6. 发布门槛

在上述外部依赖完成并留存证据前，版本只能标记为 staging/UAT。正式上线需要重新执行本报告中的脚本、真实依赖验收和灾备演练，并由业务、财务和安全负责人签字。
