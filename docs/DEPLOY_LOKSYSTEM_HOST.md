# 在现有 LokSystem 服务器部署 LokToken

本文用于把 LokToken 部署到已经运行 LokSystem 的服务器。两套系统共享主机和公网入口，但必须保持应用进程、数据库、配置文件和数据卷隔离。

## 推荐拓扑

```text
token.lokai.net.cn
        |
        v
现有 Nginx 或阿里云负载均衡（HTTPS）
        |
        v
LokToken 容器 127.0.0.1:8000
        |
        v
独立 PostgreSQL 容器和 loktoken_postgres_data 数据卷
```

LokSystem 现有域名、端口、数据库和容器不需要修改。建议使用 `token.lokai.net.cn` 作为 LokToken 的唯一入口，管理控制台、用户中心和 OpenAI 兼容 API 共用该域名。

## 上线前提

- 服务器已安装 Docker Engine 和 Compose v2。
- `lokai.net.cn` 已完成备案，DNS 可新增 `token` 子域名。
- 已准备真实 PostgreSQL 密码、管理员初始化密钥、供应商加密密钥、安全 Webhook 密钥和 OIDC 配置。
- 已准备至少一个真实供应商的受限测试 Key；生产 Key 不要写入 Git。
- 已确认服务器有至少 2 vCPU / 4 GB RAM 可供 LokToken 使用，并有独立备份空间。

## 部署步骤

1. 在 DNS 控制台新增 `token.lokai.net.cn`，指向现有服务器公网 IP 或阿里云负载均衡。
2. 在项目目录复制配置模板：

   ```bash
   cp .env.docker.example .env.docker
   chmod 600 .env.docker
   ```

3. 编辑 `.env.docker`，至少替换数据库密码、管理员密钥、`TOKEN_PROVIDER_SECRETS_KEY`、支付回调密钥、安全 Webhook 密钥、OIDC 客户端密钥和真实供应商 Key。设置：

   ```dotenv
   TOKEN_ENVIRONMENT=production
   TOKEN_AUTO_CREATE_SCHEMA=false
   TOKEN_MOCK_MODE=false
   TOKEN_SEED_BUILTIN_MODELS=false
   TOKEN_PUBLIC_BASE_URL=https://token.lokai.net.cn
   TOKEN_CORS_ORIGINS=https://token.lokai.net.cn
   TOKEN_LOKSYSTEM_SSO_ENABLED=false
   ```

4. 使用独立 Compose 项目名启动，避免与 LokSystem 的容器、网络和卷重名：

   ```bash
   docker compose -p loktoken --env-file .env.docker up -d --build
   docker compose -p loktoken --env-file .env.docker ps
   ```

5. 检查迁移和健康状态：

   ```bash
   docker compose -p loktoken --env-file .env.docker run --rm token alembic current
   curl -fsS https://token.lokai.net.cn/healthz
   curl -fsS https://token.lokai.net.cn/readyz
   ```

   `readyz` 返回失败时，不要把域名切给用户；先检查 PostgreSQL 健康状态和容器日志。

6. 复制 `deploy/nginx/loktoken.conf.example` 到 Nginx 配置目录，替换域名和证书路径，执行 `nginx -t` 后 reload。证书应通过阿里云证书服务或 Certbot 自动续期。

## 首次初始化与模型发布

首次启动只使用一次 `TOKEN_ADMIN_TOKEN` 调用 `/admin/auth/bootstrap` 创建超级管理员。之后改用管理员账号会话，不再使用初始化密钥。

在管理控制台中完成以下顺序：配置供应商密钥 -> 检查渠道 -> 运行模型预检 -> 确认价格 -> 发布模型。只有真实健康检查和预检通过的模型才允许出现在用户中心。

## 备份与回滚

每天备份 PostgreSQL，至少保留 7 天；备份文件不得与应用容器共用数据卷。发布前记录当前镜像版本和 Alembic revision。回滚时按以下顺序执行：

```bash
docker compose -p loktoken --env-file .env.docker pull
docker compose -p loktoken --env-file .env.docker up -d
docker compose -p loktoken --env-file .env.docker logs --tail=200 token
```

数据库迁移不可逆时，不要直接降级；先恢复备份到独立数据库进行验证，再切换流量。任何真实扣费或订单问题都必须保留请求 ID、订单号和账本流水作为审计证据。

## 验收门槛

部署后必须在预发布或小范围灰度用户中完成 [UAT_PREPROD.md](UAT_PREPROD.md) 的全部步骤，重点确认：真实模型非流式/流式调用、Token 统计、余额扣减与失败回滚、备用渠道切换、管理员权限、密码重置和订单对账。未完成真实供应商验证前，不得把候选模型批量发布给用户。
