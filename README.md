# LokSystem TOKEN 1.2

TOKEN 是 loksystem 的统一模型调用和用量计费平台 MVP，当前版本为 1.2.0。

TOKEN 1.2 面向 LokSystem 用户提供统一模型调用、账户额度、API Key、试用接入、充值订单和兑换福利。它与后续观测运营平台保持独立边界：TOKEN 只保存调用元数据和计费结果，不保存模型输入或输出正文。

## 快速开始

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

- LokToken管理控制台：`http://127.0.0.1:8000/`
- LokToken用户中心：`http://127.0.0.1:8000/portal`
- 管理文档：`http://127.0.0.1:8000/guide/admin`
- 用户文档：`http://127.0.0.1:8000/guide/user`
- OpenAPI 开发参考：`http://127.0.0.1:8000/docs`

默认开启 `TOKEN_MOCK_MODE=true`，不需要真实模型供应商即可验证完整调用链路。生产环境应关闭 Mock，并通过 `provider_api_key_env` 引用环境变量中的供应商密钥，不要把密钥写入数据库。

开发与试用环境会自动初始化 `lok-chat`、`lok-reason`、`lok-vision` 三个 LokSystem 内置模型。它们可立即使用 API Key 走完整的统一调用、计费、请求记录与渠道健康检查链路；Mock 返回仅用于联调验证，不代表真实模型推理。生产环境默认不创建这些模型，需在管理后台为真实上游完成模型和渠道配置后再对用户公开。

内置模型调用示例（将 `$key.key` 替换为用户自己的 API Key）：

```powershell
$headers = @{ Authorization = "Bearer $($key.key)" }
Invoke-RestMethod http://127.0.0.1:8000/v1/chat/completions -Method Post -Headers $headers -ContentType "application/json" -Body '{"model":"lok-chat","messages":[{"role":"user","content":"你好，请介绍一下 LokSystem TOKEN"}]}'
```

用户中心的“模型广场”会展示模型提供方、能力标签、上下文长度、输入/输出价格、支持参数和当前 API 限流，并支持按文本、推理、代码、视觉和工具调用筛选。点击模型卡片可查看模型 ID、cURL 和 Python SDK 调用示例；示例中的 `YOUR_API_KEY` 只需替换为用户自己的 Key。

## 用户试用闭环 v0.2

用户可以直接注册账号或使用管理员发放的限时试用链接：

```text
POST /auth/register   {"login_id":"demo-user","name":"Demo User","password":"至少 8 位"}
POST /auth/login      {"login_id":"demo-user","password":"至少 8 位"}
```

注册或登录返回 `usr_...` 用户会话令牌；管理员试用链接返回 `trl_...` 试用令牌。两者都通过 `Authorization: Bearer <token>` 访问用户中心，但只有试用令牌会把新建 API Key 的有效期绑定到试用到期时间。会话令牌和试用令牌均有服务端签名和过期时间，账户停用后立即失效。

新用户的完整使用路径为：注册或登录 -> 模型广场选择公开模型 -> 在兑换福利领取额度或创建充值申请 -> 创建 API Key -> 调用 `/v1/chat/completions` -> 在请求记录查看结果与费用。该路径已有端到端回归测试覆盖；新注册账户默认余额为 0，不会绕过额度校验。

管理员可为已有 LokSystem 账户生成限时用户中心链接：

```powershell
$headers = @{ "X-Admin-Token" = "change-me" }
$body = @{ account_id = 1; expires_in_seconds = 604800 } | ConvertTo-Json
$trial = Invoke-RestMethod http://127.0.0.1:8000/admin/trial-links -Method Post -Headers $headers -ContentType "application/json" -Body $body
$trial.portal_url
```

也可以在管理后台的“账户”页面点击“试用链接”。链接中的签名令牌位于 URL Fragment，不会被发送到服务端访问日志；用户中心读取后会立即从地址栏清除，并保存在当前标签页的 `sessionStorage` 中。

用户中心提供个人余额、接入地址、API Key、模型价格、用量、额度管理、充值订单和兑换福利。概览页支持按 7/30/90 天、模型和 API Key 查看费用、Token、请求趋势、模型排行和近一年活动热力图。所有 `/portal/*` 数据均按签名令牌绑定的账户隔离。

创建 API Key 时可以设置有效天数和累计消费额度。平台会在请求预扣阶段同时检查账户余额和 Key 剩余额度，结算时按真实消费回退差额；用户中心会显示已用额度、到期时间和最近使用时间。

个人用量支持按模型、API Key 和时间范围筛选，并可导出最多 5000 条账户隔离的 CSV 记录。导出内容包含请求 ID、Trace ID、Token、耗时、费用、状态和错误信息。

## 多渠道高可用路由 v0.3

每个公开模型可以配置多个上游渠道，公开模型继续承载统一价格，渠道切换不会改变用户看到的模型名或计费价格。创建模型时平台会自动生成 `Primary` 主渠道，管理员可在“模型与渠道”页面继续新增备用渠道，并配置：

- 优先级：数值越小越先尝试。
- 权重：相同优先级内按权重分配请求顺序。
- 独立上游地址、上游模型和 API Key 环境变量。
- 启停状态、健康状态、连续失败数与熔断截止时间。

同步请求遇到网络错误、`408`、`409`、`425`、`429` 或供应商 `5xx` 时，会在 `TOKEN_MAX_CHANNEL_ATTEMPTS` 限制内自动尝试下一渠道。渠道连续失败达到阈值后会临时熔断，冷却结束后自动重新参与路由。流式请求仅在尚未向客户端发送任何数据时切换渠道，一旦已输出内容就不会重放请求。

管理员可以通过界面或以下接口管理渠道：

```text
GET   /admin/models/{model_id}/channels
POST  /admin/models/{model_id}/channels
PATCH /admin/channels/{channel_id}
POST  /admin/channels/{channel_id}/check
```

主动健康检查调用渠道的 `GET /models`。生产环境建议根据供应商限流策略合理设置检查频率，并通过 `TOKEN_CHANNEL_FAILURE_THRESHOLD`、`TOKEN_CHANNEL_CIRCUIT_COOLDOWN_SECONDS`、`TOKEN_MAX_CHANNEL_ATTEMPTS` 和超时配置调整故障转移策略。

管理员可以一次检查所有启用渠道：

```text
POST /admin/models/health-check
```

返回已检查、健康和异常渠道数量；用户模型广场会同步显示健康、待检测、部分异常或暂不可用状态。

## 模型接入与定价

管理员在“模型与渠道”中可采用两种方式接入模型：

- 单模型：为公开模型指定上游模型、统一输入/输出价格和 Primary 渠道。
- 批量接入：填写上游 OpenAI 兼容 API 地址与已部署的密钥环境变量名，读取 `/models` 目录后选择多个模型统一导入。

批量导入仅在服务端读取环境变量中的上游密钥；浏览器、数据库和用户中心均不会接触密钥明文。每个公开模型的默认 Primary 渠道可在“渠道”中继续扩展备用上游、优先级、权重和独立健康检查。用户中心只展示 TOKEN 的公开模型名称和价格。

管理接口：

```text
GET   /admin/upstream-models?provider_base_url=...&provider_api_key_env=...
POST  /admin/models/batch
PATCH /admin/models/{model_id}
```

## Docker + PostgreSQL 部署

仓库已提供 PostgreSQL 17、Alembic 数据库迁移和 TOKEN 服务的 Compose 配置。首次部署：

```powershell
Copy-Item .env.docker.example .env.docker
# 编辑 .env.docker，至少替换数据库密码、管理令牌和支付回调密钥。
docker compose --env-file .env.docker up --build -d
docker compose --env-file .env.docker ps
```

启动后访问：

- LokToken管理控制台：`http://127.0.0.1:8000/`
- LokToken用户中心：`http://127.0.0.1:8000/portal`
- 管理文档：`http://127.0.0.1:8000/guide/admin`
- 用户文档：`http://127.0.0.1:8000/guide/user`
- OpenAPI 开发参考：`http://127.0.0.1:8000/docs`

容器启动时会先执行 `alembic upgrade head`，迁移成功后再启动 API。生产配置固定使用 `TOKEN_AUTO_CREATE_SCHEMA=false`，数据库结构必须由 Alembic 管理；PostgreSQL 数据保存在 `token_postgres_data` volume 中。

生产部署前请完成以下配置：

- 为 `POSTGRES_PASSWORD`、`TOKEN_ADMIN_TOKEN`、`TOKEN_PAYMENT_WEBHOOK_SECRET`、`TOKEN_TRIAL_SIGNING_SECRET` 设置互不相同的高强度随机值。
- 将 `TOKEN_MOCK_MODE` 改为 `false`，配置真实模型网关地址和供应商 API Key。
- 在公网入口配置 HTTPS、访问控制、日志采集和数据库备份。
- 不要提交 `.env.docker`、供应商密钥、商户私钥或证书到版本库。
- `TOKEN_ENVIRONMENT=production` 下，应用会拒绝启动：Mock 模式、自动建表、HTTP 公网地址、默认或过短的管理/签名密钥均不允许进入生产。
- 在公网入口终止 TLS，并将 `TOKEN_PUBLIC_BASE_URL` 设置为实际 HTTPS 域名；浏览器跨域调用时仅将可信域写入 `TOKEN_CORS_ORIGINS`。
- TOKEN 内置的是单服务实例的进程内滑动窗口限流。横向扩容前，应在网关/WAF 层配置共享限流或将该能力接入集中式存储。

查看迁移状态或手动执行迁移：

```powershell
docker compose --env-file .env.docker run --rm token alembic current
docker compose --env-file .env.docker run --rm token alembic upgrade head
```

停止服务时使用 `docker compose --env-file .env.docker down`。只有明确需要删除全部 PostgreSQL 数据时才使用带 `--volumes` 的命令。

## 管理接口

管理接口使用 `X-Admin-Token`：

```powershell
$headers = @{ "X-Admin-Token" = "change-me" }
$account = Invoke-RestMethod http://127.0.0.1:8000/admin/accounts -Method Post -Headers $headers -ContentType "application/json" -Body '{"external_user_id":"lok-user-001","name":"Demo User"}'
$keyBody = @{ name = "demo"; account_id = $account.id } | ConvertTo-Json
$key = Invoke-RestMethod http://127.0.0.1:8000/admin/api-keys -Method Post -Headers $headers -ContentType "application/json" -Body $keyBody
Invoke-RestMethod http://127.0.0.1:8000/admin/models -Method Post -Headers $headers -ContentType "application/json" -Body '{"public_name":"demo-model","upstream_model":"gpt-4o-mini","input_price_micros_per_1k":1500,"output_price_micros_per_1k":6000}'

# 给账户充值。amount_micros 使用平台内部的百万分之一货币单位。
Invoke-RestMethod "http://127.0.0.1:8000/admin/accounts/$($account.id)/balance" -Method Post -Headers $headers -ContentType "application/json" -Body '{"amount_micros":1000000,"idempotency_key":"demo-topup-001"}'
```

## OpenAI-compatible 调用

```powershell
$headers = @{ Authorization = "Bearer $($key.key)" }
Invoke-RestMethod http://127.0.0.1:8000/v1/chat/completions -Method Post -Headers $headers -ContentType "application/json" -Body '{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}'
```

流式响应使用 OpenAI 兼容 SSE：

```powershell
curl.exe -N http://127.0.0.1:8000/v1/chat/completions `
  -H "Authorization: Bearer $($key.key)" `
  -H "Content-Type: application/json" `
  -d '{"model":"demo-model","messages":[{"role":"user","content":"hello"}],"stream":true}'
```

费用以 `amount_micros` 保存，避免浮点误差。`request_id`、`trace_id` 和用量记录已为后续运营运维平台预留。

每次请求会按输入估算量和输出预算预扣余额，完成后按照真实 Token 数结算并退回差额；供应商失败会自动释放预扣。用户可以通过 `GET /v1/account` 查询当前余额。同步与 SSE 流式响应都使用同一套预扣、结算和渠道故障转移逻辑。

同一账户可以创建多个 API Key，并共享账户余额。管理员可以通过 `GET /admin/accounts/{account_id}/transactions` 查询最近 100 条账户流水。旧的 `/admin/api-keys/{api_key_id}/balance` 与交易查询接口继续可用，并自动映射到该 Key 所属账户。充值接口的 `idempotency_key` 用于防止支付回调重试造成重复入账。

## 支付订单

正式充值建议使用支付订单流程：

1. `POST /admin/payment-orders` 创建待支付订单。
2. 支付渠道向 `POST /payments/webhook` 发送支付成功通知。
3. TOKEN 幂等确认订单，并写入账户余额和 `payment` 类型账本流水。
4. 管理员可通过 `POST /admin/payment-orders/{order_id}/refund` 发起全额退款。

回调请求体使用 `TOKEN_PAYMENT_WEBHOOK_SECRET` 进行 HMAC-SHA256 签名，并通过 `X-Token-Signature: sha256=<hex>` 传入。`/admin/payment-orders/{order_id}/confirm` 仅用于人工确认或联调环境。直接余额充值接口继续保留，用于线下调整，不应代替线上支付订单。

当前只有 `manual`（人工确认）支付渠道可用。微信支付和支付宝会在管理后台显示为“未接入”，平台不会接受这两个渠道的订单；相关环境变量仅是后续适配器的配置占位。正式接入需要商户号、应用 ID、私钥和平台证书，并实现渠道签名验签、异步通知校验以及支付链接或二维码生成。

## 发布检查

1. 使用 `.env.docker.example` 创建 `.env.docker`，替换全部密码和签名密钥，并保留 `TOKEN_MOCK_MODE=false`。
2. 以空 PostgreSQL 数据库启动 Compose，让容器执行 `alembic upgrade head`；不要在已由开发模式自动建表的 SQLite 文件上直接执行首次 Alembic 升级。
3. 确认 `GET /healthz` 返回存活、`GET /readyz` 返回数据库就绪，并完成至少一个模型渠道的健康检查。
4. 创建测试账户、试用链接和限额 API Key，验证模型调用、用量记录、充值确认与兑换核销。
5. 通过 `GET /admin/audit-events` 检查关键管理和门户操作的审计记录；日志中不应包含 API Key、兑换码明文或模型正文。

## 兑换福利

运营人员可创建福利兑换码，兑换码明文只会在创建接口响应中返回一次，平台仅保存其哈希值。用户在“兑换福利”中成功核销后，额度会立即计入当前账户，并生成独立的余额流水和兑换历史。同一账户不能重复领取同一码，兑换码也支持总领取次数与失效时间限制。

```powershell
$headers = @{ "X-Admin-Token" = "change-me" }
$body = @{ label = "内测福利"; amount_micros = 5000000; max_redemptions = 100 } | ConvertTo-Json
$code = Invoke-RestMethod http://127.0.0.1:8000/admin/redemption-codes -Method Post -Headers $headers -ContentType "application/json" -Body $body
$code.code # 请仅通过受控渠道发放；此值之后无法恢复
```

相关接口：

```text
POST /admin/redemption-codes
GET  /admin/redemption-codes
GET  /portal/balance-summary
POST /portal/redemption-codes/redeem
GET  /portal/redemptions
```
