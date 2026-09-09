# 企业级网关能力落地说明

本轮已将六类商业化能力拆成可运行基础：

- 多协议入口：`POST /v1/responses`、`POST /v1/messages`、`POST /v1beta/models/{model}:generateContent`，统一转换到内部 Chat 契约；非流式和 SSE 流式事件均提供协议适配，生产仍需用官方 SDK 做 golden 验收。
- 路由解释：usage 保存 `route_decision_json`、失败分类和渠道尝试；管理员可通过 `GET /admin/traces/{trace_id}` 查询完整链路。
- 治理：账户、组织、项目预算在预扣阶段校验；全局、账户、组织、项目、Key 采用分层并发租约，Key 支持模型白名单和在线策略更新。
- 缓存：`TOKEN_CACHE_ENABLED=true` 后支持内存或 Redis 响应缓存，缓存按账户/Key 隔离，命中仍走预扣、结算和 usage 流水。
- 异步任务：任务保存尝试次数、下次重试时间、失败类别和死信时间；后台 worker 使用数据库租约避免多实例重复执行，超过 `TOKEN_TASK_MAX_ATTEMPTS` 自动退款并进入死信状态；管理员可查询任务，操作员可重放未结算死信。
- 企业与财务：已有 OIDC 主链上增加 SCIM 用户同步端点；增加发票申请/开具状态和收入分成记录模型。真实身份中心、电子发票服务、支付渠道和分账结算仍需外部接入。

## 生产配置

新增配置包括：

```text
TOKEN_CACHE_ENABLED=false
TOKEN_CACHE_TTL_SECONDS=30
TOKEN_MAX_CONCURRENT_REQUESTS=100
TOKEN_MAX_CONCURRENT_PER_KEY=8
TOKEN_TASK_WORKER_INTERVAL_SECONDS=10
TOKEN_TASK_MAX_ATTEMPTS=3
TOKEN_SCIM_ENABLED=false
TOKEN_SCIM_BEARER_TOKEN=
```

生产环境必须使用 Redis，缓存需根据数据敏感性和供应商条款启用；SCIM Token 应放入密钥管理系统，不能写入仓库或日志。

## 外部待办

1. 使用真实 OpenAI Responses、Anthropic、Gemini 官方 SDK 完成非流式和流式 golden 测试，并确认工具调用、图像输入和供应商错误事件语义。
2. 为 Redis 缓存、限流、并发和 worker 执行两实例压测与故障演练。
3. 接入正式 IdP 的 SCIM/SSO、用户组到组织角色的映射和离职禁用回收。
4. 对接真实电子发票平台、支付退款、月结账单和分账出款渠道。
5. 接入 OpenTelemetry Collector、长期指标存储和告警值班规则。
