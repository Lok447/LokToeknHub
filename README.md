# LokToken 1.2

LokToken 是独立的模型网关、统一 API 与用量计费平台，当前版本为 1.2.0。

LokToken 面向个人、团队和第三方应用提供统一模型调用、账户额度、API Key、试用接入、充值订单和兑换福利。它与具体身份系统和上游供应商解耦，只保存调用元数据和计费结果，不保存模型输入或输出正文。

P0 生产网关基础已经补充 64 位微元账本、供应商请求与路由追踪、缓存/推理 Token、供应商成本、OpenAI 常用参数透传、模型元数据和 Prometheus 最小指标。生产环境通过 `TOKEN_MIN_REAL_PROVIDER_COUNT`（示例值为 3）约束真实发布供应商数量；未满足真实渠道、价格和健康检查时 `/admin/runtime` 的 `release_ready` 会保持 `false`。

P1 运营治理已补充实时告警：管理概览和 `GET /admin/alerts` 会检查低余额、渠道熔断/异常、近期请求失败率、供应商成本倒挂和待处理订单，并标记是否阻断发布。告警按当前数据库数据计算，不依赖常驻调度器；生产环境可先接入 Prometheus，再将这些告警码映射到企业通知渠道。

告警评估会以稳定指纹写入 `alert_incidents`，应用默认每 60 秒执行一次；同一告警持续存在时只更新状态，不重复通知，首次触发、重新触发和恢复时通过安全通知 Webhook 投递。可通过 `POST /admin/alerts/evaluate` 手动执行并查看投递结果。Webhook 使用 `X-LokToken-Signature` HMAC-SHA256 签名和 `X-LokToken-Event` 事件类型头，失败投递保持待处理并在后续周期重试。

管理控制台“用量管理 → 供应商账单”支持导入归一化 JSON 账单。`POST /admin/provider-bills/import` 按 `provider_request_id`（缺失时可用平台 `request_id` 作为 `line_key`）逐笔核对 Token 和供应商成本，输出 `matched`、`mismatch`、`unmatched` 三种状态；相同供应商和相同内容哈希的账单不会重复导入。成本以微元传入，允许差异阈值由 `TOKEN_PROVIDER_BILL_COST_TOLERANCE_MICROS` 配置。

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

从新用户注册到模型调用、以及管理控制台商用验收的完整手册见 [docs/LOKTOKEN_USER_MANUAL.md](docs/LOKTOKEN_USER_MANUAL.md)。

按产品分层、角色、业务闭环和成熟度整理的能力地图见 [docs/LOKTOKEN_PRODUCT_CAPABILITY_MAP.md](docs/LOKTOKEN_PRODUCT_CAPABILITY_MAP.md)。

默认关闭 Mock（`TOKEN_MOCK_MODE=false`），服务只调用已配置的真实供应商。测试需要联调模拟链路时，必须在测试环境显式设置 `TOKEN_MOCK_MODE=true` 和 `TOKEN_SEED_BUILTIN_MODELS=true`；生产环境不得启用这两个开关。供应商密钥通过 `provider_api_key_env` 引用环境变量，不写入数据库。

### 真实数据口径与本地 UAT 清理

管理概览的余额、请求、Token、消费、账户和渠道状态均来自数据库账本与当前渠道检测结果，不由前端静态生成。`TOKEN_MOCK_MODE=false` 只代表请求走真实供应商链路；`TOKEN_ENVIRONMENT=development` 仍表示开发环境，历史 UAT/Smoke 请求不应被当作生产流量。

本地完成 UAT 后，可先预览带有明确测试标识的账户，再执行清理。脚本只删除这些账户及其 Key、请求、余额流水、订单和登录关联，保留模型配置、供应商渠道和审计事件：

```powershell
python scripts/cleanup_uat_data.py
python scripts/cleanup_uat_data.py --execute
```

生产环境不要使用该脚本；生产数据应通过环境隔离、数据库权限和正式迁移管理。

管理台的 DeepSeek 预设已提供 `deepseek-v4-flash`（`DeepSeek-V4-Flash-0731`）、`deepseek-v4-pro`（`DeepSeek-V4-Pro-0813`）与 `deepseek-v4-flash-vision-exp`（`DeepSeek-V4-Flash-Vision-Exp`，实验性视觉候选）。安装后模型保持停用，管理员需要在服务端配置 `DEEPSEEK_API_KEY`，执行渠道检测和预检，再启用模型；视觉候选还需单独核验当前目录和官方价格。旧的 `lok-*` 模拟模型在非 Mock 启动时会自动停用，不会进入用户中心。

真实模型调用示例（将 `$key.key` 替换为用户自己的 API Key）：

```powershell
$headers = @{ Authorization = "Bearer $($key.key)" }
Invoke-RestMethod http://127.0.0.1:8000/v1/chat/completions -Method Post -Headers $headers -ContentType "application/json" -Body '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"你好，请介绍一下 LokToken"}]}'
```

用户中心的“模型广场”会展示模型提供方、能力标签、上下文长度、输入/输出价格、支持参数和当前 API 限流，并支持按模型类型、服务商、健康状态和价格排序筛选。点击模型卡片可查看模型 ID、cURL 和 Python SDK 调用示例；示例中的 `YOUR_API_KEY` 只需替换为用户自己的 Key。文本对话模型详情还提供“测试调用”入口：选择账户下有效的 API Key、输入简短提示词即可在页面内验证模型，测试请求会按当前平台价格消耗额度并写入请求记录；没有有效 Key 时需先创建 Key。

模型发布采用“候选 -> 已发布/Mock 已发布 -> 下架”的显式闭环。发布前必须满足价格、适配器、启用渠道和健康检查条件，可使用 `POST /admin/models/{model_id}/publish` 与 `POST /admin/models/{model_id}/unpublish` 管理状态；`GET /admin/models/{model_id}/history` 提供价格、配置和发布变更记录。图像、视频和音频模型的测试入口会创建生成任务并按任务价格结算。音频适配器提供 `POST /v1/audio/speech`（语音合成）和 `POST /v1/audio/transcriptions`（语音识别），识别请求支持 Base64 音频或供应商可访问的音频 URL；异步供应商仍可通过统一任务查询接口获取结果。渠道健康记录包含检测来源、状态码、延迟、最近错误和连续失败次数，管理端与用户中心均可查看相应明细。

## 用户试用闭环 v0.2

用户可以直接注册账号或使用管理员发放的限时试用链接：

```text
POST /auth/register   {"login_id":"demo-user","name":"Demo User","password":"至少 8 位"}
POST /auth/login      {"login_id":"demo-user","password":"至少 8 位"}
```

注册或登录返回 `usr_...` 用户会话令牌；管理员试用链接返回 `trl_...` 试用令牌。两者都通过 `Authorization: Bearer <token>` 访问用户中心，但只有试用令牌会把新建 API Key 的有效期绑定到试用到期时间。会话令牌和试用令牌均有服务端签名和过期时间，账户停用后立即失效。

新用户的完整使用路径为：注册或登录 -> 模型广场选择公开模型 -> 在兑换福利领取额度或创建充值申请 -> 创建 API Key -> 调用 `/v1/chat/completions` -> 在请求记录查看结果与费用。该路径已有端到端回归测试覆盖；新注册账户默认余额为 0，不会绕过额度校验。

管理员可为已有账户生成限时用户中心链接：

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

初始化时会自动写入 DeepSeek、Qwen / DashScope、GLM / 智谱、Kimi / Moonshot、MiniMax 和 Doubao / 火山方舟的常用文本、多模态、图像与视频模型目录，并为每个模型生成停用的主渠道。Qwen 首批目录按 DashScope 真实 `/models` 结果维护，包含 Qwen3.8 Max、2.4T A95B、27B、Qwen3.7 Plus、Qwen3 Coder Next/Plus、Qwen3-VL Flash/Plus、Qwen Image 3.0/Pro，以及 Wan 视频任务候选。旧版 Qwen Plus、Turbo、Max、VL Max、Image Plus 和无法被目录识别的泛化 ID 已退出预置目录；无调用历史的旧候选会在启动时自动清理。该过程可重复执行且不会覆盖已有同名模型；可通过 `TOKEN_SEED_PROVIDER_CATALOGUE=false` 关闭。每个模型都会挂载对应服务商的官方定价来源；已核验的 DeepSeek/Qwen 模型支持按官方价格和利润率自动生成平台售价，其他厂家先显示官方链接和“待核验”，管理员确认模型级价格后再录入，不会把链接误当作价格，也不会自动发布。管理员只需在模型管理中补充密钥与价格、启用渠道、执行健康检测和预检，不必逐个创建模型。

管理控制台和用户中心按市场习惯展示文本模型的“元 / 1M Token”价格。后端账本继续使用 `*_micros_per_1k` 字段保存微元/1K Token，以兼容已有 API 和结算记录；控制台会自动完成两种单位之间的换算。

除价格外，模型目录还保存服务商适配参数：协议类型、鉴权方式、模型发现路径、调用路径、流式传输格式、usage 来源和参数策略。当前六家文本服务商均核验为 Bearer + OpenAI Chat Completions + SSE 的兼容基线，但仍按模型保留能力边界；图像/视频模型使用异步任务协议并保持待适配。未经官方核验的厂家专属参数不会自动透传，避免把一个厂家的参数误发给另一个厂家。

请求进入网关前会依据模型能力契约校验任务类型和最大输出 Token，并按服务商配置执行安全的参数别名转换（例如 `max_completion_tokens` 转为上游兼容的 `max_tokens`）。模型详情和 `/v1/models` 会返回协议、流式、鉴权、支持参数和最大输出限制，便于 SDK 在客户端提前校验；不满足契约的图像/视频请求会明确返回待适配错误，不会被当作聊天请求转发。

DeepSeek V4 模板按官网人民币价格保存参考价，并以未命中缓存、低峰价格作为平台默认计费价；运营仍可在定价操作中调整人民币售价。供应商密钥可在管理控制台加密录入，或通过 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`ZHIPU_API_KEY`、`MOONSHOT_API_KEY`、`MINIMAX_API_KEY`、`ARK_API_KEY` 注入。图像和视频模型当前先进入候选目录，在 `/v1/images/generations` 与视频任务适配器、独立计费规则上线前会保持“待完善”，不会被错误地暴露为聊天模型。

批量导入仅在服务端读取环境变量中的上游密钥；浏览器、数据库和用户中心均不会接触密钥明文。每个公开模型的默认 Primary 渠道可在“渠道”中继续扩展备用上游、优先级、权重和独立健康检查。用户中心只展示 TOKEN 的公开模型名称和价格。

管理控制台的“模型管理”支持服务商级接入。进入 DeepSeek、Qwen、GLM、Kimi、MiniMax 或 Doubao 卡片，填写一次服务商 API 地址、密钥（或服务器环境变量）即可读取服务商真实 `/models` 目录，为该系列候选模型复用同一个加密连接并更新渠道健康状态。服务商接入只负责连接、同步和余额预警，不再填写批量售价；同步后必须在模型列表的“定价”入口逐个维护 LokToken 面向用户的输入/输出价格。定价弹窗按模型所属服务商显示对应官方价格链接、币种、计费单位和阶梯参数：已核验价格的模型支持直接填写目标利润率，按“官方价格 ÷（1 - 利润率）”自动生成输入/输出平台售价；仅有官方链接的模型必须先人工核验再手工定价。服务商采购成本在“渠道”中独立维护，不能用平台售价替代。同步不会自动发布模型，运营人员应完成真实预检、采购成本核对和价格审核后再单独发布；重复提交会执行同步而不是重复创建模型，已存在的单模型密钥也会在首次服务商同步时平滑迁移。图像、视频模型仍会保留在目录中，直到对应统一调用适配器和计费规则上线。

DeepSeek、Qwen、GLM、Kimi、MiniMax、Doubao 等内置服务商卡片由预置目录维护，不依赖数据库中是否仍有模型。即使某个服务商的模型被全部删除，卡片、筛选入口和服务商接入配置仍会保留，并显示“尚未同步模型”，方便运营人员重新同步该系列。

服务商卡片还提供上游余额管理。DeepSeek 使用官方余额 API 查询；Qwen、GLM、Kimi、MiniMax、Doubao 等服务商若当前没有可验证的公开余额 API，页面会明确显示“需控制台查询”，运营人员可从服务商控制台读取余额后手工录入。余额快照包含金额、币种、来源、查询时间和错误状态，可设置采购预警阈值；上游余额与 LokToken 用户额度完全隔离，不会被模型健康检查推断。

图像/视频生成模型采用任务型接入，不与文本 `/v1/chat/completions` 共用调用协议：图像模型使用 `POST /v1/images/generations`，视频模型使用 `POST /v1/videos/generations` 创建任务并通过 `GET /v1/generation-tasks/{task_id}` 轮询。任务模型按张或按次配置平台售价，创建时预扣额度，供应商失败时自动退回预扣额度；文本、图像与视频模型均由各自协议返回结果，互不伪装为聊天模型。服务商仍需先核验模型目录、配置单次任务价格并启用健康渠道，才可上架。

管理接口：

```text
GET   /admin/upstream-models?provider_base_url=...&provider_api_key_env=...
POST  /admin/models/batch
PATCH /admin/models/{model_id}
GET   /admin/provider-connections
PUT   /admin/provider-connections/{preset_id}
POST  /admin/provider-connections/{preset_id}/balance/refresh
POST  /admin/provider-connections/{preset_id}/balance/manual
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

- 为 `POSTGRES_PASSWORD`、`TOKEN_ADMIN_TOKEN`、`TOKEN_PAYMENT_WEBHOOK_SECRET`、`TOKEN_TRIAL_SIGNING_SECRET` 和 `TOKEN_SECURITY_DELIVERY_WEBHOOK_SECRET` 设置互不相同的高强度随机值。
- 将 `TOKEN_MOCK_MODE` 改为 `false`，配置真实模型网关地址和供应商 API Key。
- 在公网入口配置 HTTPS、访问控制、日志采集和数据库备份。
- 不要提交 `.env.docker`、供应商密钥、商户私钥或证书到版本库。
- `TOKEN_ENVIRONMENT=production` 下，应用会拒绝启动：Mock 模式、自动建表、HTTP 公网地址、默认或过短的管理/签名密钥均不允许进入生产。
- 配置 `TOKEN_SECURITY_DELIVERY_MODE=webhook` 与 HTTPS 安全投递 Webhook；密码重置仅发送给已绑定安全联系方式的账户，Webhook 接收方需验证 `X-LokToken-Signature`。
- 在公网入口终止 TLS，并将 `TOKEN_PUBLIC_BASE_URL` 设置为实际 HTTPS 域名；浏览器跨域调用时仅将可信域写入 `TOKEN_CORS_ORIGINS`。
- TOKEN 内置的是单服务实例的进程内滑动窗口限流。横向扩容前，应在网关/WAF 层配置共享限流或将该能力接入集中式存储。

查看迁移状态或手动执行迁移：

```powershell
docker compose --env-file .env.docker run --rm token alembic current
docker compose --env-file .env.docker run --rm token alembic upgrade head
```

停止服务时使用 `docker compose --env-file .env.docker down`。只有明确需要删除全部 PostgreSQL 数据时才使用带 `--volumes` 的命令。

## 管理员账号与会话

首次部署仅使用一次 `TOKEN_ADMIN_TOKEN` 创建超级管理员；创建成功后该密钥不能再访问管理接口。后续管理接口使用管理员账号登录后取得的 Bearer 会话令牌。管理员角色包括：`superadmin`（全量）、`operator`（运营）和 `auditor`（只读）。

模型供应商密钥可以通过管理控制台录入，服务端使用 `TOKEN_PROVIDER_SECRETS_KEY` 加密保存，只返回配置状态，不会回显明文；也可以继续使用部署环境变量 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`ZHIPU_API_KEY`、`MOONSHOT_API_KEY`、`MINIMAX_API_KEY` 和 `ARK_API_KEY`。生产环境必须设置独立且至少 32 位的 `TOKEN_PROVIDER_SECRETS_KEY`，不要把它提交到 Git。预置候选仍需启用渠道、执行真实健康检测、确认平台价格并通过预检，才能正式上架。

```powershell
$bootstrap = Invoke-RestMethod http://127.0.0.1:8000/admin/auth/bootstrap -Method Post -Headers @{ "X-Admin-Token" = "change-me" } -ContentType "application/json" -Body '{"login_id":"admin","password":"replace-with-a-strong-password"}'
$headers = @{ "Authorization" = "Bearer $($bootstrap.access_token)" }
$account = Invoke-RestMethod http://127.0.0.1:8000/admin/accounts -Method Post -Headers $headers -ContentType "application/json" -Body '{"name":"Demo API Account"}'
$keyBody = @{ name = "demo"; account_id = $account.id } | ConvertTo-Json
$key = Invoke-RestMethod http://127.0.0.1:8000/admin/api-keys -Method Post -Headers $headers -ContentType "application/json" -Body $keyBody
Invoke-RestMethod http://127.0.0.1:8000/admin/models -Method Post -Headers $headers -ContentType "application/json" -Body '{"public_name":"demo-model","upstream_model":"gpt-4o-mini","input_price_micros_per_1k":1500,"output_price_micros_per_1k":6000}'

# 给账户充值。amount_micros 使用平台内部的百万分之一货币单位。
Invoke-RestMethod "http://127.0.0.1:8000/admin/accounts/$($account.id)/balance" -Method Post -Headers $headers -ContentType "application/json" -Body '{"amount_micros":1000000,"idempotency_key":"demo-topup-001"}'
```

在现有 LokSystem 服务器上的同机部署步骤见 [docs/DEPLOY_LOKSYSTEM_HOST.md](docs/DEPLOY_LOKSYSTEM_HOST.md)，预发布执行步骤与发布门槛见 [docs/UAT_PREPROD.md](docs/UAT_PREPROD.md)。

## LokSystem 统一账号

本地桌面联调默认启用 LokSystem 一键注册 / 登录。LokToken 只会回连 `TOKEN_LOKSYSTEM_SSO_BASE_URL` 指向的本机回环地址，向已登录的 LokSystem 桌面端申请 60 秒、单次可用的票据；票据在 LokToken 服务端交换，不会出现在浏览器地址、日志、密码字段或 API Key 中。首次进入自动创建 LokToken 账户，之后按 LokSystem 用户 ID 自动识别并登录。此方式仅适合本机桌面联调；跨设备或生产环境应使用下方的 OIDC 集成。

LokToken 可作为 OpenID Connect 客户端接入 LokSystem 身份中心。启用后，用户中心会出现“LokSystem 一键注册 / 登录”，并通过授权码 + PKCE 完成登录。首次登录会按 LokSystem 的稳定用户标识自动创建 LokToken 账户；后续登录会复用同一账户。LokToken 只保存 `issuer + subject` 身份映射，额度、订单、API Key 和模型权限仍保留在 LokToken。

配置 `TOKEN_OIDC_ENABLED=true` 后，必须提供 LokSystem 身份中心的 issuer、客户端凭据、授权端点、令牌端点、UserInfo 端点和回调地址。建议 LokSystem 在 UserInfo 中提供稳定的 `lok_user_id`，并通过 `TOKEN_OIDC_ACCOUNT_ID_CLAIM=lok_user_id` 将其映射到 LokToken 的 `external_user_id`。首次登录可自动创建账户；设置 `TOKEN_OIDC_ALLOW_ACCOUNT_CREATION=false` 后，只允许已经由运营人员创建或已绑定的账户登录。

跨设备或生产部署时，`.env.docker.example` 默认启用 OIDC，并保持 `TOKEN_LOKSYSTEM_SSO_ENABLED=false`。在 LokSystem 身份中心为 `TOKEN_OIDC_REDIRECT_URI` 注册 HTTPS 回调地址后，填入真实的 `TOKEN_OIDC_CLIENT_ID`、`TOKEN_OIDC_CLIENT_SECRET` 与各 OIDC 端点。应用会拒绝以不完整、非 HTTPS 的 OIDC 配置启动，也会拒绝在生产环境启用本机桌面 SSO。

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
