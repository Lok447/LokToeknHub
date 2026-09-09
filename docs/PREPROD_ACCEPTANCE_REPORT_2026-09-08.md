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

### DeepSeek 真实供应商 Golden Test

使用临时 DeepSeek 凭据完成了真实供应商验证，凭据未写入代码、配置或报告：

- `GET /v1/models`：200，成功发现 `deepseek-v4-flash`、`deepseek-v4-pro` 等模型。
- 真实模型健康检查和发布门禁：通过。
- Chat HTTP、SSE、Responses、Anthropic、Gemini：全部 200。
- DeepSeek usage 字段被保留并参与网关计量。

故障演练使用伪造的 DeepSeek `/fail/v1` 路径时返回 404。网关将其分类为不可重试上游错误并终止请求，符合当前失败分类策略，但没有覆盖真实 5xx/超时切换。后续需通过供应商 Sandbox、网络策略或故障注入代理注入 429/5xx/超时，再验证备用路由、熔断和重试。

### 故障注入代理演练

使用新增的 `scripts/fault_injection_proxy.py` 将主通道映射到注入 500 的本地代理，备用通道保持 DeepSeek 真实地址。连续两次请求均成功返回，Trace 明确记录主通道 `500 / upstream_5xx` 后切换到备用通道 `200`；注入通道累计失败次数达到 2，尚未达到配置的熔断阈值 3。补充演练中，429 被分类为 `rate_limit` 后切换备用通道；超时被分类为 `timeout` 后切换备用通道；连续第 3 次 500 后注入通道设置 `circuit_open_until` 并被后续请求跳过。演练结束后已停用注入通道，避免污染预生产模型路由。

负向场景中停止模拟供应商后，健康检查将模型标记为 `unhealthy`、发布状态为 `blocked`，请求返回 503；恢复供应商后再次通过，证明发布门禁和恢复路径有效。

## 4. 本轮修复

1. 修复空 PostgreSQL 部署中 `generation_tasks` 缺少建表迁移的问题，完善 `0030_gateway_reliability`。
2. 新增 `0033_schema_completeness`，补齐 ORM 与生产迁移之间遗漏的字段、索引和 `model_change_records` 表。
3. `/readyz` 增加关键表字段检查，迁移不完整时返回 503。
4. 双实例 Compose 拆出一次性 `migrate` 服务，应用实例不再并发执行 Alembic。
5. 基础 Compose 补齐缓存、并发、Worker 配置，确保多实例配置一致。
6. 预生产脚本改为可重复执行，认证、账户和模型失败时安全停止并写报告。

## 5. 上线前待办

### Worker 闭环修复（2026-09-09）

- `save_usage` 对相同 `request_id` 幂等返回，避免重复 Worker 或轮询造成重复 UsageRecord。
- 任务结算增加任务行锁和 `settled_at` 保护，成功结算、失败退款只能执行一次。
- Worker 异常按 HTTP 状态分类为 `rate_limit`、`timeout`、`upstream_5xx` 等，不再统一标记为 `worker`。
- 任务轮询遇到临时供应商错误时进入指数退避；达到最大尝试次数后才进入死信并退款。
- 死信 replay 会重新预扣、生成新请求号和 Trace，并重置尝试状态；正常已结算任务仍禁止 replay。

代码回归测试结果：`76 passed`。

### 双实例 Worker 演练结果（2026-09-09）

- 预生产双实例、PostgreSQL 和 Redis 均已启动，迁移头为 `0033_schema_completeness`，两实例 `/readyz` 返回 200。
- 真实预扣后制造不可达异步任务，Worker 两次尝试后进入 `failed`/死信，`failure_class=upstream_5xx`。
- 失败任务仅产生一笔 reservation 和一笔 settlement 退款，余额恢复到预扣前；UsageRecord 仅一条。
- 管理员 replay 成功生成新的 request/trace，新增一笔 reservation；再次失败后新增且仅新增一笔 settlement，账务无重复结算。
- 两个实例先后执行 Worker 对同一任务的处理，最终 `attempt_count` 仍为单一任务的受控次数，未产生重复 UsageRecord 或重复 settlement；租约字段处理完成后会被清理。
- 实例停止演练：`token` 容器停止后，任务在租约过期后再次被处理并进入死信；唯一 reservation 对应唯一 settlement，UsageRecord 仅一条。由于本轮未记录持租约实例身份，也未在上游请求阻塞期间执行进程级 `SIGKILL`，因此该结果只能证明“容器停止后任务最终可恢复处理”，不能证明持租约进程崩溃接管已被确定性验证。

本次容器演练已覆盖失败、死信、replay、账务幂等和容器停止后的最终恢复处理。进程级租约接管仍是上线阻断项。

- 使用每个实际供应商的 sandbox/官方 SDK 完成模型、流式、超时、429、5xx、计费和退款 Golden Test。
- 接入真实企业 IdP，验证 OIDC 登录、SCIM 创建/更新/停用、组和权限映射，完成密钥轮换演练。
- 接入真实支付商户和电子发票服务，验证签名、公钥轮换、退款、对账、发票开具和重试。
- 部署 OpenTelemetry Collector、指标长期存储、日志脱敏、告警通知和 SLO 看板，并进行故障演练。
- 进行 Redis/PostgreSQL 高可用、备份恢复、跨可用区和滚动升级演练。
- 对异步图像/音频/视频任务制造失败任务，验证重试、租约抢占、死信、人工 replay、退款和审计闭环。
- 增加确定性 Worker 接管演练：记录租约持有实例身份，在上游请求阻塞时对持有实例执行 `SIGKILL`，确认租约到期前无重复处理、到期后仅一个实例接管，并核对最终结算与 UsageRecord 幂等性。
- 使用正式域名、TLS、WAF、密钥管理系统和生产级安全扫描结果替换本地 UAT 配置。

## 6. 发布门槛

在上述外部依赖完成并留存证据前，版本只能标记为 staging/UAT。正式上线需要重新执行本报告中的脚本、真实依赖验收和灾备演练，并由业务、财务和安全负责人签字。

