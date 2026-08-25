import asyncio
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
import httpx
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import record_audit_event
from .config import cors_origin_list, get_settings, validate_startup_settings
from .db import SessionLocal, engine, get_db, init_db
from .guardrails import rate_limiter
from .model_release import channel_credentials_configured as _channel_credentials_configured, model_is_callable, model_publication_state as _model_publication_state
from .metrics import observe_request, render_prometheus
from .models import AccountBalanceTransaction, AdminSession, AdminUser, AlertIncident, ApiKey, AuditEvent, BillingAccount, ExternalIdentity, GenerationTask, ModelChannel, ModelConfig, Organization, OrganizationMember, PasswordResetChallenge, PaymentOrder, Project, ProviderBalanceSnapshot, ProviderBillImport, ProviderBillLine, ProviderConnection, RedemptionClaim, RedemptionCode, SecurityContactChallenge, SecurityNotification, UsageRecord, Workspace, utcnow
from .payments import mark_order_paid, refund_order
from .payment_providers import payment_providers, require_available_provider
from .portal import router as portal_router
from .provider_presets import DEPRECATED_PROVIDER_MODEL_PUBLIC_NAMES, get_provider_preset, provider_catalogue_matches, provider_preset_data, PROVIDER_PRESETS
from .provider_secrets import ProviderSecretError, decrypt_provider_secret, encrypt_provider_secret
from .schemas import AccountBalance, AccountCreate, ActiveUpdate, AdminLogin, AdminUserCreate, AdminUserUpdate, ApiKeyCreate, ApiKeyResponse, BalanceAdjust, ChatCompletionRequest, ImageGenerationRequest, ModelBatchImport, ModelChannelCreate, ModelChannelUpdate, ModelCreate, ModelPreflightRequest, ModelUpdate, PaymentConfirm, PaymentOrderCreate, PaymentRefund, PaymentWebhook, ProviderBalanceManual, ProviderBillImportRequest, ProviderConnectionConfigure, ProviderPresetInstall, RedemptionCodeCreate, UsageSummary, VideoGenerationRequest
from .security import AdminContext, create_admin_session, create_key, create_redemption_code, hash_key, hash_password, require_admin, require_api_key, require_bootstrap_admin_token, require_finance_operator, require_operator, require_superadmin, verify_password, verify_webhook_signature
from .services import calculate_amount, call_provider, call_provider_details, check_channel_health, create_provider_task, credit_balance, discover_upstream_models, estimate_tokens, fetch_provider_balance, normalize_request_payload, provider_cost, refresh_provider_task, reserve_balance, save_usage, settle_balance, stream_provider, validate_model_request
from .workspaces import ensure_default_project, ensure_personal_workspace

@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_startup_settings(get_settings())
    init_db()
    if not get_settings().auto_create_schema:
        with engine.connect() as connection:
            connection.execute(select(1))
    alert_task = asyncio.create_task(alert_evaluation_loop())
    try:
        yield
    finally:
        alert_task.cancel()
        try:
            await alert_task
        except asyncio.CancelledError:
            pass


async def alert_evaluation_loop() -> None:
    """Evaluate operational alerts periodically without blocking requests."""
    while True:
        try:
            await asyncio.to_thread(run_alert_evaluation)
        except Exception:
            # Delivery state stays pending and will be retried on the next tick.
            pass
        await asyncio.sleep(get_settings().alert_evaluation_interval_seconds)


def run_alert_evaluation() -> None:
    with SessionLocal() as db:
        evaluate_alert_incidents(db, deliver=True)


app = FastAPI(title="TOKEN Platform", version="1.2.0", lifespan=lifespan)
if cors_origin_list(get_settings()):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origin_list(get_settings()),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Admin-Token", "X-Request-ID", "X-Trace-ID", "X-Token-Signature"],
        expose_headers=["X-Request-ID", "X-Trace-ID", "Retry-After"],
    )
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.include_router(portal_router)

_request_id_pattern = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _openai_error_type(status_code: int, detail: str) -> tuple[str, str]:
    """Return an OpenAI-compatible error type and stable machine code."""
    if detail == "insufficient balance":
        return "insufficient_balance", "insufficient_balance"
    if detail == "api key spending limit exceeded":
        return "quota_exceeded", "quota_exceeded"
    if status_code == 401:
        return "authentication_error", "invalid_api_key"
    if status_code == 403:
        return "permission_error", "permission_denied"
    if status_code == 404 and detail.startswith("unknown model:"):
        return "invalid_request_error", "model_not_found"
    if status_code == 503 and detail.startswith("model unavailable:"):
        return "server_error", "model_unavailable"
    if status_code == 409:
        return "invalid_request_error", "request_conflict"
    if status_code == 429:
        return "rate_limit_error", "rate_limit_exceeded"
    if status_code >= 500:
        return "server_error", "upstream_error"
    return "invalid_request_error", "invalid_request"


@app.exception_handler(HTTPException)
async def api_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Expose gateway failures in the error shape expected by OpenAI clients."""
    if not request.url.path.startswith("/v1/"):
        content = {"detail": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers or {})
    detail = str(exc.detail)
    error_type, error_code = _openai_error_type(exc.status_code, detail)
    message = "账户余额不足，请先充值或兑换额度后再调用模型。" if error_code == "insufficient_balance" else detail
    content = {
        "error": {"message": message, "type": error_type, "param": None, "code": error_code},
        # Keep the legacy field for existing integrations that read FastAPI's detail shape.
        "detail": detail,
    }
    headers = dict(exc.headers or {})
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)


@app.middleware("http")
async def production_response_headers(request: Request, call_next):
    supplied_request_id = request.headers.get("x-request-id", "")
    correlation_id = supplied_request_id if _request_id_pattern.fullmatch(supplied_request_id) else "req_" + uuid.uuid4().hex
    request.state.correlation_id = correlation_id
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > get_settings().max_request_body_bytes:
                return JSONResponse(status_code=413, content={"detail": "request body too large"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "invalid content length"})
    response = await call_next(request)
    observe_request(request.url.path, request.method, response.status_code)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("X-Request-ID", correlation_id)
    if request.url.path in {"/", "/portal", "/static/app.js", "/static/portal.js", "/static/portal.css", "/static/styles.css"} or request.url.path.startswith("/admin/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/metrics", include_in_schema=False)
def metrics(db: Session = Depends(get_db)) -> PlainTextResponse:
    business_metrics = {
        "loktoken_usage_success_total": db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.status == "success")) or 0,
        "loktoken_usage_error_total": db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.status == "error")) or 0,
        "loktoken_revenue_micros_total": db.scalar(select(func.coalesce(func.sum(UsageRecord.amount_micros), 0)).where(UsageRecord.status == "success")) or 0,
        "loktoken_provider_cost_micros_total": db.scalar(select(func.coalesce(func.sum(UsageRecord.provider_cost_micros), 0)).where(UsageRecord.status == "success")) or 0,
        "loktoken_channels_healthy": db.scalar(select(func.count(ModelChannel.id)).where(ModelChannel.active.is_(True), ModelChannel.status == "healthy")) or 0,
        "loktoken_channels_unhealthy": db.scalar(select(func.count(ModelChannel.id)).where(ModelChannel.active.is_(True), ModelChannel.status == "unhealthy")) or 0,
    }
    payload = render_prometheus() + "\n".join(f"# TYPE {name} gauge\n{name} {value}" for name, value in business_metrics.items()) + "\n"
    return PlainTextResponse(payload, media_type="text/plain; version=0.0.4")


@app.get("/", include_in_schema=False)
def console() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/portal", include_in_schema=False)
def user_portal() -> FileResponse:
    return FileResponse(static_dir / "portal.html")


@app.get("/guide/{audience}", include_in_schema=False)
def product_guide(audience: str) -> FileResponse:
    if audience not in {"admin", "user"}:
        raise HTTPException(status_code=404, detail="guide not found")
    return FileResponse(static_dir / "guide.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(select(1))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}


def payment_order_data(order: PaymentOrder, account_name: str | None = None) -> dict[str, object]:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "account_id": order.account_id,
        "account_name": account_name,
        "amount_micros": order.amount_micros,
        "provider": order.provider,
        "provider_order_id": order.provider_order_id,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "refunded_at": order.refunded_at.isoformat() if order.refunded_at else None,
        "reviewed_by_admin_id": order.reviewed_by_admin_id,
        "reviewed_at": order.reviewed_at.isoformat() if order.reviewed_at else None,
        "review_note": order.review_note,
    }


def admin_user_data(user: AdminUser) -> dict[str, object]:
    return {
        "id": user.id,
        "login_id": user.login_id,
        "role": user.role,
        "active": user.active,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat(),
    }


def admin_auth_response(user: AdminUser, db: Session) -> dict[str, object]:
    token, session = create_admin_session(db, user)
    db.flush()
    user.last_login_at = utcnow()
    record_audit_event(db, actor_type="admin", actor_id=user.login_id, action="admin.session_created", target_type="admin_session", target_id=session.id, details={"role": user.role})
    db.commit()
    db.refresh(session)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": session.expires_at.isoformat(),
        "admin": admin_user_data(user),
    }


@app.post("/admin/auth/bootstrap")
def bootstrap_admin(
    payload: AdminUserCreate,
    _: None = Depends(require_bootstrap_admin_token),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if db.scalar(select(AdminUser.id).limit(1)) is not None:
        raise HTTPException(status_code=409, detail="administrator bootstrap is no longer available")
    user = AdminUser(login_id=payload.login_id.lower(), password_hash=hash_password(payload.password), role="superadmin")
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="administrator login already exists") from exc
    record_audit_event(db, actor_type="bootstrap", actor_id="bootstrap-admin", action="admin.bootstrap_created", target_type="admin_user", target_id=user.id, details={"login_id": user.login_id})
    return admin_auth_response(user, db)


@app.post("/admin/auth/login")
def login_admin(payload: AdminLogin, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("admin-login", request.client.host if request.client else "unknown", settings.auth_rate_limit_requests, settings.auth_rate_limit_window_seconds)
    user = db.scalar(select(AdminUser).where(AdminUser.login_id == payload.login_id.lower()))
    if not user or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid administrator credentials")
    return admin_auth_response(user, db)


@app.post("/admin/auth/logout", dependencies=[Depends(require_admin)])
def logout_admin(context: AdminContext = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    if context.session:
        context.session.revoked_at = utcnow()
        record_audit_event(db, actor_type="admin", actor_id=context.actor_id, action="admin.session_revoked", target_type="admin_session", target_id=context.session.id, details={"self": True})
        db.commit()
    return {"revoked": True}


@app.get("/admin/auth/me", dependencies=[Depends(require_admin)])
def admin_identity(context: AdminContext = Depends(require_admin)) -> dict[str, object]:
    if context.bootstrap:
        return {"bootstrap": True, "role": context.role, "login_id": context.actor_id}
    return {"bootstrap": False, "admin": admin_user_data(context.user)}


@app.get("/admin/users", dependencies=[Depends(require_superadmin)])
def list_admin_users(db: Session = Depends(get_db)) -> dict[str, object]:
    users = db.scalars(select(AdminUser).order_by(AdminUser.id)).all()
    return {"data": [admin_user_data(user) for user in users]}


@app.post("/admin/users", dependencies=[Depends(require_superadmin)])
def create_admin_user(payload: AdminUserCreate, context: AdminContext = Depends(require_superadmin), db: Session = Depends(get_db)) -> dict[str, object]:
    user = AdminUser(login_id=payload.login_id.lower(), password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="administrator login already exists") from exc
    record_audit_event(db, actor_type="admin", actor_id=context.actor_id, action="admin.user_created", target_type="admin_user", target_id=user.id, details={"login_id": user.login_id, "role": user.role})
    db.commit()
    db.refresh(user)
    return admin_user_data(user)


@app.patch("/admin/users/{admin_user_id}", dependencies=[Depends(require_superadmin)])
def update_admin_user(admin_user_id: int, payload: AdminUserUpdate, context: AdminContext = Depends(require_superadmin), db: Session = Depends(get_db)) -> dict[str, object]:
    user = db.get(AdminUser, admin_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="administrator not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="no administrator changes provided")
    if context.user and context.user.id == user.id and changes.get("active") is False:
        raise HTTPException(status_code=422, detail="administrator cannot deactivate their own account")
    for field, value in changes.items():
        setattr(user, field, value)
    if changes.get("active") is False:
        user.session_version += 1
        for session in db.scalars(select(AdminSession).where(AdminSession.admin_user_id == user.id, AdminSession.revoked_at.is_(None))).all():
            session.revoked_at = utcnow()
    record_audit_event(db, actor_type="admin", actor_id=context.actor_id, action="admin.user_updated", target_type="admin_user", target_id=user.id, details=changes)
    db.commit()
    db.refresh(user)
    return admin_user_data(user)


@app.get("/admin/runtime", dependencies=[Depends(require_admin)])
def admin_runtime(db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    models = db.scalars(select(ModelConfig)).all()
    published = 0
    mock_published = 0
    candidates = 0
    blocked = 0
    published_provider_hosts: set[str] = set()
    credential_envs = sorted({
        channel.provider_api_key_env
        for channel in db.scalars(select(ModelChannel)).all()
        if channel.provider_api_key_env
    })
    provider_credentials = [
        {"env": env_name, "configured": bool(os.getenv(env_name, "").strip())}
        for env_name in credential_envs
    ]
    for model in models:
        channels = db.scalars(select(ModelChannel).where(ModelChannel.model_config_id == model.id)).all()
        state, _ = _model_publication_state(model, channels, settings)
        if state == "published":
            published += 1
            published_provider_hosts.update(channel.provider_base_url.rstrip("/") for channel in channels if channel.active and channel.health_source == "provider")
        elif state == "mock_published":
            mock_published += 1
        elif state == "candidate":
            candidates += 1
        else:
            blocked += 1
    alerts = build_operational_alerts(db)
    gateway_ready = (published > 0 and len(published_provider_hosts) >= settings.min_real_provider_count) if not settings.mock_mode else mock_published > 0
    release_blocking_alert_count = sum(1 for alert in alerts if alert["release_blocking"])
    return {
        "environment": settings.environment,
        "mock_mode": settings.mock_mode,
        "seed_builtin_models": settings.seed_builtin_models,
        "data_mode": "mock" if settings.mock_mode else "real",
        "published_model_count": published,
        "mock_published_model_count": mock_published,
        "candidate_model_count": candidates,
        "blocked_model_count": blocked,
        "provider_credentials": provider_credentials,
        "published_provider_count": len(published_provider_hosts),
        "minimum_real_provider_count": settings.min_real_provider_count,
        "gateway_ready": gateway_ready,
        "release_ready": gateway_ready and release_blocking_alert_count == 0,
        "operational_alert_count": len(alerts),
        "release_blocking_alert_count": release_blocking_alert_count,
    }


@app.get("/admin/overview", dependencies=[Depends(require_admin)])
def admin_overview(db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    active_channels = db.scalar(select(func.count(ModelChannel.id)).where(ModelChannel.active.is_(True))) or 0
    healthy_channels = db.scalar(select(func.count(ModelChannel.id)).where(ModelChannel.active.is_(True), ModelChannel.status == "healthy")) or 0
    unhealthy_channels = db.scalar(select(func.count(ModelChannel.id)).where(ModelChannel.active.is_(True), ModelChannel.status == "unhealthy")) or 0
    models = db.scalars(select(ModelConfig)).all()
    model_states = []
    for model in models:
        channels = db.scalars(select(ModelChannel).where(ModelChannel.model_config_id == model.id)).all()
        model_states.append(_model_publication_state(model, channels, settings)[0])
    alerts = build_operational_alerts(db)
    return {
        "environment": settings.environment,
        "mock_mode": settings.mock_mode,
        "account_count": db.scalar(select(func.count(BillingAccount.id))) or 0,
        "active_key_count": db.scalar(select(func.count(ApiKey.id)).where(ApiKey.active.is_(True))) or 0,
        "active_model_count": db.scalar(select(func.count(ModelConfig.id)).where(ModelConfig.active.is_(True))) or 0,
        "total_balance_micros": db.scalar(select(func.coalesce(func.sum(BillingAccount.balance_micros), 0))) or 0,
        "request_count": db.scalar(select(func.count(UsageRecord.id))) or 0,
        "total_tokens": db.scalar(select(func.coalesce(func.sum(UsageRecord.total_tokens), 0))) or 0,
        "amount_micros": db.scalar(select(func.coalesce(func.sum(UsageRecord.amount_micros), 0))) or 0,
        "active_channel_count": active_channels,
        "healthy_channel_count": healthy_channels,
        "unhealthy_channel_count": unhealthy_channels,
        "pending_payment_count": db.scalar(select(func.count(PaymentOrder.id)).where(PaymentOrder.status == "pending")) or 0,
        "published_model_count": sum(state == "published" for state in model_states),
        "mock_published_model_count": sum(state == "mock_published" for state in model_states),
        "candidate_model_count": sum(state == "candidate" for state in model_states),
        "blocked_model_count": sum(state == "blocked" for state in model_states),
        "alerts": alerts,
        "alert_count": len(alerts),
        "release_blocking_alert_count": sum(1 for alert in alerts if alert["release_blocking"]),
    }


def build_operational_alerts(db: Session) -> list[dict[str, object]]:
    """Compute actionable P1 signals from durable platform data.

    Alerts are deliberately calculated on read so a deployment does not need a
    scheduler before operators can see a degraded channel or billing risk.
    """
    settings = get_settings()
    now = utcnow()
    alerts: list[dict[str, object]] = []

    def add(code: str, severity: str, title: str, detail: str, count: int = 1, *, release_blocking: bool = False, action: str = "") -> None:
        alerts.append({
            "code": code,
            "severity": severity,
            "title": title,
            "detail": detail,
            "count": count,
            "release_blocking": release_blocking,
            "action": action,
            "observed_at": now.isoformat(),
        })

    low_balance_count = db.scalar(
        select(func.count(BillingAccount.id)).where(
            BillingAccount.active.is_(True),
            BillingAccount.balance_micros <= settings.alert_low_balance_micros,
        )
    ) or 0
    if low_balance_count:
        add(
            "low_balance",
            "warning",
            "账户余额预警",
            f"{low_balance_count} 个活跃账户余额低于 {settings.alert_low_balance_micros / 1_000_000:.2f} 元。",
            low_balance_count,
            action="发放额度或提醒用户充值",
        )

    expired_key_count = db.scalar(
        select(func.count(ApiKey.id)).where(ApiKey.active.is_(True), ApiKey.expires_at.is_not(None), ApiKey.expires_at <= now)
    ) or 0
    if expired_key_count:
        add(
            "expired_keys",
            "critical",
            "存在已过期 API Key",
            f"{expired_key_count} 个仍标记为启用的 API Key 已超过有效期。",
            expired_key_count,
            action="停用过期 Key 并通知所属账户",
        )
    expiring_key_count = db.scalar(
        select(func.count(ApiKey.id)).where(
            ApiKey.active.is_(True),
            ApiKey.expires_at.is_not(None),
            ApiKey.expires_at > now,
            ApiKey.expires_at <= now + timedelta(days=7),
        )
    ) or 0
    if expiring_key_count:
        add(
            "expiring_keys",
            "warning",
            "API Key 即将过期",
            f"{expiring_key_count} 个 API Key 将在 7 天内过期。",
            expiring_key_count,
            action="提醒用户轮换 Key 或延长有效期",
        )

    unhealthy_count = db.scalar(select(func.count(ModelChannel.id)).where(ModelChannel.active.is_(True), ModelChannel.status == "unhealthy")) or 0
    if unhealthy_count:
        add(
            "unhealthy_channels",
            "critical",
            "模型渠道异常",
            f"{unhealthy_count} 个启用渠道健康检查失败或已触发熔断。",
            unhealthy_count,
            release_blocking=True,
            action="检查密钥、上游地址并执行健康检查",
        )

    window_start = now - timedelta(minutes=settings.alert_lookback_minutes)
    request_count = db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.created_at >= window_start)) or 0
    error_count = db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.created_at >= window_start, UsageRecord.status == "error")) or 0
    failure_rate = (error_count / request_count * 100) if request_count else 0.0
    if request_count >= settings.alert_min_request_count and failure_rate >= settings.alert_failure_rate_percent:
        add(
            "failure_rate",
            "critical",
            "近期请求失败率过高",
            f"最近 {settings.alert_lookback_minutes} 分钟 {request_count} 次请求中有 {error_count} 次失败（{failure_rate:.1f}%）。",
            error_count,
            release_blocking=True,
            action="检查渠道状态、上游限流和最近错误记录",
        )

    loss_count = db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.created_at >= window_start, UsageRecord.status == "success", UsageRecord.provider_cost_micros > UsageRecord.amount_micros)) or 0
    if loss_count:
        add(
            "cost_anomaly",
            "critical",
            "供应商成本高于平台售价",
            f"最近窗口内 {loss_count} 次成功请求的供应商成本超过平台收费，存在倒挂风险。",
            loss_count,
            release_blocking=True,
            action="核对供应商价格和模型售价后再继续放量",
        )

    pending_count = db.scalar(select(func.count(PaymentOrder.id)).where(PaymentOrder.status == "pending")) or 0
    if pending_count:
        add(
            "pending_orders",
            "info",
            "待运营处理订单",
            f"当前有 {pending_count} 个充值订单等待确认。",
            pending_count,
            action="进入订单管理完成确认或拒绝",
        )
    return alerts


def alert_fingerprint(alert: dict[str, object]) -> str:
    """Keep fingerprints stable while allowing the count/detail to change."""
    raw = f"{alert['code']}|{alert['severity']}|{alert['release_blocking']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def deliver_alert_event(event: str, incident: AlertIncident) -> bool:
    settings = get_settings()
    if settings.security_delivery_mode == "development":
        return True
    if settings.security_delivery_mode != "webhook" or not settings.security_delivery_webhook_url or len(settings.security_delivery_webhook_secret) < 16:
        return False
    body = json.dumps({
        "event": event,
        "incident_id": incident.id,
        "fingerprint": incident.fingerprint,
        "code": incident.code,
        "severity": incident.severity,
        "title": incident.title,
        "detail": incident.detail,
        "action": incident.action,
        "count": incident.count,
        "state": incident.state,
        "first_seen_at": incident.first_seen_at.isoformat(),
        "last_seen_at": incident.last_seen_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(settings.security_delivery_webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    try:
        response = httpx.post(
            settings.security_delivery_webhook_url,
            content=body,
            headers={"Content-Type": "application/json", "X-LokToken-Signature": signature, "X-LokToken-Event": event},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def evaluate_alert_incidents(db: Session, *, deliver: bool = False) -> dict[str, object]:
    """Upsert alert incidents and emit only state transitions."""
    now = utcnow()
    current = {alert_fingerprint(item): item for item in build_operational_alerts(db)}
    incidents = {item.fingerprint: item for item in db.scalars(select(AlertIncident)).all()}
    emitted: list[dict[str, object]] = []
    for fingerprint, alert in current.items():
        incident = incidents.get(fingerprint)
        if incident is None:
            incident = AlertIncident(
                fingerprint=fingerprint,
                code=str(alert["code"]),
                severity=str(alert["severity"]),
                title=str(alert["title"]),
                detail=str(alert["detail"]),
                action=str(alert.get("action") or ""),
                count=int(alert.get("count") or 1),
                state="active",
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(incident)
            db.flush()
            incident.pending_event = "alert_opened"
            emitted.append({"event": "alert_opened", "incident": incident})
        else:
            reopened = incident.state == "resolved"
            incident.code = str(alert["code"])
            incident.severity = str(alert["severity"])
            incident.title = str(alert["title"])
            incident.detail = str(alert["detail"])
            incident.action = str(alert.get("action") or "")
            incident.count = int(alert.get("count") or 1)
            incident.state = "active"
            incident.last_seen_at = now
            incident.resolved_at = None
            if reopened:
                incident.pending_event = "alert_reopened"
                emitted.append({"event": "alert_reopened", "incident": incident})
    for fingerprint, incident in incidents.items():
        if fingerprint not in current and incident.state == "active":
            incident.state = "resolved"
            incident.resolved_at = now
            incident.last_seen_at = now
            incident.pending_event = "alert_recovered"
            emitted.append({"event": "alert_recovered", "incident": incident})
    db.commit()
    delivered = 0
    failed = 0
    if deliver:
        pending_incidents = db.scalars(select(AlertIncident).where(AlertIncident.pending_event.is_not(None))).all()
        for incident in pending_incidents:
            event = str(incident.pending_event)
            if deliver_alert_event(event, incident):
                incident.last_notified_state = event
                incident.notified_at = utcnow()
                incident.pending_event = None
                delivered += 1
            else:
                failed += 1
        db.commit()
    return {"active_count": len(current), "transitions": [item["event"] for item in emitted], "delivered": delivered, "failed": failed}


@app.get("/admin/alerts", dependencies=[Depends(require_admin)])
def admin_alerts(db: Session = Depends(get_db)) -> dict[str, object]:
    alerts = build_operational_alerts(db)
    incidents = evaluate_alert_incidents(db)
    return {
        "data": alerts,
        "incidents": [
            {"id": item.id, "fingerprint": item.fingerprint, "code": item.code, "state": item.state, "count": item.count, "last_seen_at": item.last_seen_at.isoformat(), "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None}
            for item in db.scalars(select(AlertIncident).order_by(AlertIncident.last_seen_at.desc()).limit(100)).all()
        ],
        "count": len(alerts),
        "release_blocking_count": sum(1 for alert in alerts if alert["release_blocking"]),
        "generated_at": utcnow().isoformat(),
        "evaluation": incidents,
    }


@app.post("/admin/alerts/evaluate", dependencies=[Depends(require_operator)])
def evaluate_and_deliver_alerts(db: Session = Depends(get_db)) -> dict[str, object]:
    return evaluate_alert_incidents(db, deliver=True)


def provider_bill_summary_data(record: ProviderBillImport) -> dict[str, object]:
    return {
        "id": record.id,
        "provider": record.provider,
        "source_name": record.source_name,
        "source_hash": record.source_hash,
        "line_count": record.line_count,
        "matched_count": record.matched_count,
        "mismatch_count": record.mismatch_count,
        "unmatched_count": record.unmatched_count,
        "billed_cost_micros": record.billed_cost_micros,
        "recorded_cost_micros": record.recorded_cost_micros,
        "difference_micros": record.billed_cost_micros - record.recorded_cost_micros,
        "created_at": record.created_at.isoformat(),
    }


@app.post("/admin/provider-bills/import", dependencies=[Depends(require_finance_operator)])
def import_provider_bill(payload: ProviderBillImportRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    """Import a normalized supplier bill and reconcile it against usage rows.

    The API intentionally accepts normalized JSON rather than provider-specific
    CSV files. A small adapter can convert each supplier export to this shape
    while keeping provider credentials and raw files outside the application.
    """
    canonical = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    source_hash = hashlib.sha256(canonical).hexdigest()
    existing = db.scalar(select(ProviderBillImport).where(ProviderBillImport.provider == payload.provider, ProviderBillImport.source_hash == source_hash))
    if existing:
        return {"duplicate": True, "import": provider_bill_summary_data(existing), "lines": []}
    record = ProviderBillImport(provider=payload.provider, source_name=payload.source_name, source_hash=source_hash, line_count=len(payload.lines), created_at=utcnow())
    db.add(record)
    db.flush()
    tolerance = get_settings().provider_bill_cost_tolerance_micros
    seen_keys: set[str] = set()
    details: list[dict[str, object]] = []
    for index, item in enumerate(payload.lines, start=1):
        line_key = item.line_key or item.provider_request_id or f"line-{index}"
        if line_key in seen_keys:
            raise HTTPException(status_code=422, detail=f"duplicate bill line key: {line_key}")
        seen_keys.add(line_key)
        usage = None
        if item.provider_request_id:
            usage = db.scalar(select(UsageRecord).where(UsageRecord.provider_request_id == item.provider_request_id).order_by(UsageRecord.id.desc()))
        if usage is None and item.line_key:
            usage = db.scalar(select(UsageRecord).where(UsageRecord.request_id == item.line_key))
        recorded_cost = usage.provider_cost_micros if usage else 0
        diff = item.billed_cost_micros - recorded_cost
        token_match = bool(usage and usage.input_tokens == item.input_tokens and usage.output_tokens == item.output_tokens)
        cost_match = bool(usage and abs(diff) <= tolerance)
        status = "matched" if usage and token_match and cost_match else "mismatch" if usage else "unmatched"
        db.add(ProviderBillLine(
            import_id=record.id,
            line_key=line_key,
            provider_request_id=item.provider_request_id,
            billed_input_tokens=item.input_tokens,
            billed_output_tokens=item.output_tokens,
            billed_cost_micros=item.billed_cost_micros,
            recorded_cost_micros=recorded_cost,
            usage_record_id=usage.id if usage else None,
            status=status,
            diff_micros=diff,
            raw_json=json.dumps(item.raw or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            created_at=utcnow(),
        ))
        record.billed_cost_micros += item.billed_cost_micros
        record.recorded_cost_micros += recorded_cost
        if status == "matched":
            record.matched_count += 1
        elif status == "mismatch":
            record.mismatch_count += 1
        else:
            record.unmatched_count += 1
        details.append({"line_key": line_key, "provider_request_id": item.provider_request_id, "usage_record_id": usage.id if usage else None, "status": status, "diff_micros": diff, "recorded_input_tokens": usage.input_tokens if usage else None, "recorded_output_tokens": usage.output_tokens if usage else None})
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="provider_bill.imported", target_type="provider_bill_import", target_id=record.id, details={"provider": record.provider, "source_name": record.source_name, "line_count": record.line_count, "mismatch_count": record.mismatch_count, "unmatched_count": record.unmatched_count})
    db.commit()
    db.refresh(record)
    return {"duplicate": False, "import": provider_bill_summary_data(record), "lines": details}


@app.get("/admin/provider-bills", dependencies=[Depends(require_admin)])
def list_provider_bills(db: Session = Depends(get_db)) -> dict[str, object]:
    records = db.scalars(select(ProviderBillImport).order_by(ProviderBillImport.id.desc()).limit(100)).all()
    return {"data": [provider_bill_summary_data(item) for item in records]}


@app.get("/admin/provider-bills/{import_id}", dependencies=[Depends(require_admin)])
def provider_bill_detail(import_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    record = db.get(ProviderBillImport, import_id)
    if not record:
        raise HTTPException(status_code=404, detail="provider bill import not found")
    lines = db.scalars(select(ProviderBillLine).where(ProviderBillLine.import_id == record.id).order_by(ProviderBillLine.id)).all()
    return {"import": provider_bill_summary_data(record), "lines": [{"id": item.id, "line_key": item.line_key, "provider_request_id": item.provider_request_id, "billed_input_tokens": item.billed_input_tokens, "billed_output_tokens": item.billed_output_tokens, "billed_cost_micros": item.billed_cost_micros, "recorded_cost_micros": item.recorded_cost_micros, "usage_record_id": item.usage_record_id, "status": item.status, "diff_micros": item.diff_micros} for item in lines]}


@app.post("/admin/payment-orders", dependencies=[Depends(require_finance_operator)])
def create_payment_order(payload: PaymentOrderCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    account = db.get(BillingAccount, payload.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=404, detail="active account not found")
    try:
        require_available_provider(payload.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project = db.get(Project, payload.project_id) if payload.project_id else None
    if payload.project_id and not project:
        raise HTTPException(status_code=404, detail="project not found")
    order = PaymentOrder(
        order_no="pay_" + uuid.uuid4().hex,
        account_id=account.id,
        workspace_id=project.workspace_id if project else None,
        project_id=project.id if project else None,
        amount_micros=payload.amount_micros,
        provider=payload.provider,
    )
    db.add(order)
    db.flush()
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="payment_order.created", target_type="payment_order", target_id=order.id, details={"order_no": order.order_no, "amount_micros": order.amount_micros, "provider": order.provider})
    db.commit()
    db.refresh(order)
    return payment_order_data(order, account.name)


@app.get("/admin/payment-providers", dependencies=[Depends(require_admin)])
def list_payment_providers() -> dict[str, object]:
    return {"data": [provider.to_dict() for provider in payment_providers()]}


@app.get("/admin/payment-orders", dependencies=[Depends(require_admin)])
def list_payment_orders(db: Session = Depends(get_db)) -> dict[str, object]:
    rows = db.execute(
        select(PaymentOrder, BillingAccount.name)
        .join(BillingAccount, BillingAccount.id == PaymentOrder.account_id)
        .order_by(PaymentOrder.id.desc())
        .limit(200)
    ).all()
    return {"data": [payment_order_data(order, account_name) for order, account_name in rows]}


@app.get("/admin/reconciliation", dependencies=[Depends(require_admin)])
def reconcile_ledger(db: Session = Depends(get_db)) -> dict[str, object]:
    account_rows = db.execute(
        select(
            BillingAccount.id,
            BillingAccount.name,
            BillingAccount.balance_micros,
            func.coalesce(func.sum(AccountBalanceTransaction.amount_micros), 0),
        )
        .outerjoin(AccountBalanceTransaction, AccountBalanceTransaction.account_id == BillingAccount.id)
        .group_by(BillingAccount.id, BillingAccount.name, BillingAccount.balance_micros)
        .order_by(BillingAccount.id)
    ).all()
    balance_mismatches = [
        {"account_id": account_id, "account_name": name, "balance_micros": balance, "ledger_micros": ledger}
        for account_id, name, balance, ledger in account_rows
        if balance != ledger
    ]
    order_issues = []
    for order in db.scalars(select(PaymentOrder).where(PaymentOrder.status.in_(("paid", "refunded"))).order_by(PaymentOrder.id)).all():
        payment_exists = db.scalar(select(AccountBalanceTransaction.id).where(AccountBalanceTransaction.reference_id == f"payment:{order.order_no}")) is not None
        refund_exists = db.scalar(select(AccountBalanceTransaction.id).where(AccountBalanceTransaction.reference_id == f"refund:{order.order_no}")) is not None
        if not payment_exists or (order.status == "refunded" and not refund_exists):
            order_issues.append({
                "order_id": order.id,
                "order_no": order.order_no,
                "status": order.status,
                "payment_recorded": payment_exists,
                "refund_recorded": refund_exists,
            })
    return {
        "ok": not balance_mismatches and not order_issues,
        "balance_mismatch_count": len(balance_mismatches),
        "order_issue_count": len(order_issues),
        "balance_mismatches": balance_mismatches,
        "order_issues": order_issues,
    }


@app.post("/admin/redemption-codes", dependencies=[Depends(require_operator)])
def admin_create_redemption_code(payload: RedemptionCodeCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    expires_at = payload.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at <= utcnow():
        raise HTTPException(status_code=422, detail="expires_at must be in the future")
    raw_code = payload.code or create_redemption_code()
    record = RedemptionCode(
        label=payload.label,
        code_prefix=raw_code[:12],
        code_hash=hash_key(raw_code),
        amount_micros=payload.amount_micros,
        max_redemptions=payload.max_redemptions,
        expires_at=expires_at,
    )
    db.add(record)
    try:
        db.flush()
        record_audit_event(
            db, actor_type="admin", actor_id="token-admin", action="redemption_code.created",
            target_type="redemption_code", target_id=record.id,
            details={"label": record.label, "amount_micros": record.amount_micros, "max_redemptions": record.max_redemptions},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="redemption code already exists") from exc
    db.refresh(record)
    return {
        "id": record.id,
        "label": record.label,
        "code": raw_code,
        "code_prefix": record.code_prefix,
        "amount_micros": record.amount_micros,
        "max_redemptions": record.max_redemptions,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
    }


@app.get("/admin/redemption-codes", dependencies=[Depends(require_admin)])
def list_redemption_codes(db: Session = Depends(get_db)) -> dict[str, object]:
    codes = db.scalars(select(RedemptionCode).order_by(RedemptionCode.id.desc()).limit(200)).all()
    return {"data": [{
        "id": item.id,
        "label": item.label,
        "code_prefix": item.code_prefix,
        "amount_micros": item.amount_micros,
        "max_redemptions": item.max_redemptions,
        "redeemed_count": item.redeemed_count,
        "active": item.active,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "created_at": item.created_at.isoformat(),
    } for item in codes]}


@app.patch("/admin/redemption-codes/{code_id}", dependencies=[Depends(require_operator)])
def update_redemption_code(code_id: int, payload: ActiveUpdate, db: Session = Depends(get_db)) -> dict[str, object]:
    record = db.get(RedemptionCode, code_id)
    if not record:
        raise HTTPException(status_code=404, detail="redemption code not found")
    record.active = payload.active
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="redemption_code.status_updated", target_type="redemption_code", target_id=record.id, details={"active": record.active})
    db.commit()
    return {"id": record.id, "active": record.active}


@app.post("/admin/payment-orders/{order_id}/confirm", dependencies=[Depends(require_finance_operator)])
def confirm_payment_order(
    order_id: int,
    payload: PaymentConfirm,
    context: AdminContext = Depends(require_finance_operator),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    order = db.get(PaymentOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="payment order not found")
    try:
        order = mark_order_paid(
            db, order, payload.provider_order_id or f"manual:{order.order_no}",
            reviewer_admin_id=context.user.id if context.user else None,
            review_note=payload.review_note,
            audit_event={"actor_type": "admin", "actor_id": context.actor_id, "action": "payment_order.confirmed", "target_type": "payment_order", "target_id": order.id, "details": {"order_no": order.order_no, "amount_micros": order.amount_micros}},
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return payment_order_data(order)


@app.post("/admin/payment-orders/{order_id}/refund", dependencies=[Depends(require_superadmin)])
def refund_payment_order(
    order_id: int,
    payload: PaymentRefund | None = None,
    context: AdminContext = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    order = db.get(PaymentOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="payment order not found")
    try:
        order = refund_order(
            db, order,
            reviewer_admin_id=context.user.id if context.user else None,
            review_note=payload.review_note if payload else None,
            audit_event={"actor_type": "admin", "actor_id": context.actor_id, "action": "payment_order.refunded", "target_type": "payment_order", "target_id": order.id, "details": {"order_no": order.order_no, "amount_micros": order.amount_micros}},
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return payment_order_data(order)


@app.post("/payments/webhook")
async def payment_webhook(
    request: Request,
    x_token_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    body = await request.body()
    if not verify_webhook_signature(body, x_token_signature):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    try:
        payload = PaymentWebhook.model_validate_json(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid webhook payload") from exc
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_no == payload.order_no))
    if not order:
        raise HTTPException(status_code=404, detail="payment order not found")
    try:
        order = mark_order_paid(
            db, order, payload.provider_order_id,
            audit_event={"actor_type": "payment_webhook", "actor_id": payload.event_id, "action": "payment_order.confirmed", "target_type": "payment_order", "target_id": order.id, "details": {"order_no": order.order_no, "amount_micros": order.amount_micros}},
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"received": True, "event_id": payload.event_id, "order": payment_order_data(order)}


@app.get("/admin/accounts", dependencies=[Depends(require_admin)])
def list_accounts(db: Session = Depends(get_db)) -> dict[str, object]:
    accounts = db.scalars(select(BillingAccount).order_by(BillingAccount.id.desc())).all()
    source_labels = {"admin": "管理员创建", "self_registered": "用户注册", "loksystem": "外部身份接入", "oidc": "统一身份接入"}
    account_types = {"admin": "客户账户", "self_registered": "个人账户", "loksystem": "外部身份账户", "oidc": "外部身份账户"}
    recent_cutoff = utcnow() - timedelta(days=30)
    data = []
    for account in accounts:
        last_usage_at = db.scalar(select(func.max(UsageRecord.created_at)).where(UsageRecord.account_id == account.id))
        last_transaction_at = db.scalar(select(func.max(AccountBalanceTransaction.created_at)).where(AccountBalanceTransaction.account_id == account.id))
        last_activity_at = max((value for value in (last_usage_at, last_transaction_at) if value), default=None)
        data.append({
            "id": account.id,
            "external_user_id": account.external_user_id,
            "login_id": account.login_id,
            "account_source": account.account_source or "admin",
            "account_source_label": source_labels.get(account.account_source, "管理员创建"),
            "account_type": account_types.get(account.account_source, "客户账户"),
            "name": account.name,
            "balance_micros": account.balance_micros,
            "active": account.active,
            "api_key_count": db.scalar(select(func.count(ApiKey.id)).where(ApiKey.account_id == account.id)) or 0,
            "project_count": db.scalar(
                select(func.count(Project.id))
                .join(Workspace, Workspace.id == Project.workspace_id)
                .where(Workspace.owner_account_id == account.id)
            ) or 0,
            "recent_spend_micros": db.scalar(
                select(func.coalesce(func.sum(UsageRecord.amount_micros), 0)).where(
                    UsageRecord.account_id == account.id,
                    UsageRecord.created_at >= recent_cutoff,
                    UsageRecord.status == "success",
                )
            ) or 0,
            "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
            "created_at": account.created_at.isoformat(),
        })
    return {"data": data}


@app.patch("/admin/accounts/{account_id}", dependencies=[Depends(require_operator)])
def update_account(account_id: int, payload: ActiveUpdate, db: Session = Depends(get_db)) -> dict[str, object]:
    account = db.get(BillingAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    account.active = payload.active
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="account.status_updated", target_type="account", target_id=account.id, details={"active": account.active})
    db.commit()
    return {"id": account.id, "active": account.active}


@app.delete("/admin/accounts/{account_id}", dependencies=[Depends(require_superadmin)])
def delete_account(account_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    account = db.get(BillingAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    if account.active:
        raise HTTPException(status_code=409, detail="请先停用账户后再删除")
    if account.balance_micros:
        raise HTTPException(status_code=409, detail="账户仍有余额，不能删除")
    if db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.account_id == account.id)):
        raise HTTPException(status_code=409, detail="该账户已有调用记录，不能删除；请保留停用状态以便审计")
    if db.scalar(select(func.count(AccountBalanceTransaction.id)).where(AccountBalanceTransaction.account_id == account.id)):
        raise HTTPException(status_code=409, detail="该账户已有账务流水，不能删除；请保留停用状态以便审计")
    if db.scalar(select(func.count(PaymentOrder.id)).where(PaymentOrder.account_id == account.id)):
        raise HTTPException(status_code=409, detail="该账户已有充值订单，不能删除；请保留停用状态以便审计")
    if db.scalar(select(func.count(RedemptionClaim.id)).where(RedemptionClaim.account_id == account.id)):
        raise HTTPException(status_code=409, detail="该账户已有福利领取记录，不能删除；请保留停用状态以便审计")
    if db.scalar(select(func.count(Organization.id)).where(Organization.owner_account_id == account.id)):
        raise HTTPException(status_code=409, detail="该账户仍是组织所有者，不能删除")

    workspace_ids = db.scalars(select(Workspace.id).where(Workspace.owner_account_id == account.id)).all()
    if workspace_ids:
        db.execute(delete(Project).where(Project.workspace_id.in_(workspace_ids)))
        db.execute(delete(Workspace).where(Workspace.id.in_(workspace_ids)))
    db.execute(delete(ApiKey).where(ApiKey.account_id == account.id))
    db.execute(delete(OrganizationMember).where(OrganizationMember.account_id == account.id))
    db.execute(delete(PasswordResetChallenge).where(PasswordResetChallenge.account_id == account.id))
    db.execute(delete(SecurityContactChallenge).where(SecurityContactChallenge.account_id == account.id))
    db.execute(delete(SecurityNotification).where(SecurityNotification.account_id == account.id))
    db.execute(delete(ExternalIdentity).where(ExternalIdentity.account_id == account.id))
    account_id_value = account.id
    external_user_id = account.external_user_id
    db.delete(account)
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="account.deleted", target_type="account", target_id=account_id_value, details={"external_user_id": external_user_id})
    db.commit()
    return {"id": account_id_value, "external_user_id": external_user_id, "deleted": True}


@app.get("/admin/api-keys", dependencies=[Depends(require_admin)])
def list_api_keys(db: Session = Depends(get_db)) -> dict[str, object]:
    rows = db.execute(
        select(ApiKey, BillingAccount.name)
        .join(BillingAccount, BillingAccount.id == ApiKey.account_id)
        .order_by(ApiKey.id.desc())
    ).all()
    return {"data": [
        {
            "id": api_key.id,
            "account_id": api_key.account_id,
            "account_name": account_name,
            "name": api_key.name,
            "key_prefix": api_key.key_prefix,
            "active": api_key.active,
            "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
            "trial_expires_at": api_key.trial_expires_at.isoformat() if api_key.trial_expires_at else None,
            "spending_limit_micros": api_key.spending_limit_micros,
            "spent_micros": api_key.spent_micros,
            "last_used_at": api_key.last_used_at.isoformat() if api_key.last_used_at else None,
            "created_at": api_key.created_at.isoformat(),
        }
        for api_key, account_name in rows
    ]}


@app.patch("/admin/api-keys/{api_key_id}", dependencies=[Depends(require_operator)])
def update_api_key(api_key_id: int, payload: ActiveUpdate, db: Session = Depends(get_db)) -> dict[str, object]:
    api_key = db.get(ApiKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="api key not found")
    api_key.active = payload.active
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="api_key.status_updated", target_type="api_key", target_id=api_key.id, details={"active": api_key.active})
    db.commit()
    return {"id": api_key.id, "active": api_key.active}


@app.post("/admin/api-keys", response_model=ApiKeyResponse, dependencies=[Depends(require_operator)])
def create_api_key(payload: ApiKeyCreate, db: Session = Depends(get_db)) -> ApiKeyResponse:
    account = db.get(BillingAccount, payload.account_id) if payload.account_id else None
    if payload.account_id and (not account or not account.active):
        raise HTTPException(status_code=404, detail="active account not found")
    if account is None:
        account = BillingAccount(external_user_id=f"standalone-{uuid.uuid4().hex}", name=payload.name, account_source="admin")
        db.add(account)
        db.flush()
    project = ensure_default_project(db, ensure_personal_workspace(db, account))
    raw_key = create_key()
    record = ApiKey(
        account_id=account.id,
        project_id=project.id,
        name=payload.name,
        key_prefix=raw_key[:12],
        key_hash=hash_key(raw_key),
        expires_at=utcnow() + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None,
        spending_limit_micros=payload.spending_limit_micros,
    )
    db.add(record)
    db.flush()
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="api_key.created", target_type="api_key", target_id=record.id, details={"account_id": record.account_id, "name": record.name})
    db.commit()
    db.refresh(record)
    return ApiKeyResponse(id=record.id, account_id=record.account_id, name=record.name, key=raw_key, key_prefix=record.key_prefix)


@app.post("/admin/accounts", dependencies=[Depends(require_operator)])
def create_account(payload: AccountCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    external_user_id = payload.external_user_id or f"admin-{uuid.uuid4().hex[:20]}"
    if db.scalar(select(BillingAccount).where(BillingAccount.external_user_id == external_user_id)):
        raise HTTPException(status_code=409, detail="external user already has an account")
    account = BillingAccount(external_user_id=external_user_id, name=payload.name, account_source="admin")
    db.add(account)
    db.flush()
    ensure_personal_workspace(db, account)
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="account.created", target_type="account", target_id=account.id, details={"external_user_id": account.external_user_id})
    db.commit()
    db.refresh(account)
    return {"id": account.id, "external_user_id": account.external_user_id, "name": account.name, "balance_micros": account.balance_micros}


@app.post("/admin/accounts/{account_id}/balance", dependencies=[Depends(require_finance_operator)])
def adjust_account_balance(account_id: int, payload: BalanceAdjust, db: Session = Depends(get_db)) -> dict[str, int]:
    account = db.get(BillingAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    reference_id = payload.idempotency_key or f"topup_{uuid.uuid4().hex}"
    try:
        account = credit_balance(
            db, account, payload.amount_micros, reference_id, payload.description,
            audit_event={"actor_type": "admin", "actor_id": "token-admin", "action": "account.balance_credited", "target_type": "account", "target_id": account.id, "details": {"amount_micros": payload.amount_micros, "reference_id": reference_id}},
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"account_id": account.id, "balance_micros": account.balance_micros}


@app.post("/admin/api-keys/{api_key_id}/balance", dependencies=[Depends(require_finance_operator)])
def adjust_balance(api_key_id: int, payload: BalanceAdjust, db: Session = Depends(get_db)) -> dict[str, int]:
    api_key = db.get(ApiKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="api key not found")
    account = db.get(BillingAccount, api_key.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    reference_id = payload.idempotency_key or f"topup_{uuid.uuid4().hex}"
    try:
        account = credit_balance(
            db, account, payload.amount_micros, reference_id, payload.description, api_key.id,
            audit_event={"actor_type": "admin", "actor_id": "token-admin", "action": "api_key.balance_credited", "target_type": "api_key", "target_id": api_key.id, "details": {"amount_micros": payload.amount_micros, "reference_id": reference_id}},
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"api_key_id": api_key.id, "balance_micros": account.balance_micros}


@app.get("/admin/api-keys/{api_key_id}/transactions", dependencies=[Depends(require_admin)])
def list_balance_transactions(api_key_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    api_key = db.get(ApiKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="api key not found")
    transactions = db.scalars(
        select(AccountBalanceTransaction)
        .where(AccountBalanceTransaction.account_id == api_key.account_id)
        .order_by(AccountBalanceTransaction.id.desc())
        .limit(100)
    ).all()
    return {
        "data": [
            {
                "id": item.id,
                "amount_micros": item.amount_micros,
                "type": item.transaction_type,
                "reference_id": item.reference_id,
                "description": item.description,
                "created_at": item.created_at.isoformat(),
            }
            for item in transactions
        ]
    }


@app.get("/admin/accounts/{account_id}/transactions", dependencies=[Depends(require_admin)])
def list_account_transactions(account_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    if not db.get(BillingAccount, account_id):
        raise HTTPException(status_code=404, detail="account not found")
    transactions = db.scalars(
        select(AccountBalanceTransaction)
        .where(AccountBalanceTransaction.account_id == account_id)
        .order_by(AccountBalanceTransaction.id.desc())
        .limit(100)
    ).all()
    return {
        "data": [
            {
                "id": item.id,
                "api_key_id": item.api_key_id,
                "amount_micros": item.amount_micros,
                "type": item.transaction_type,
                "reference_id": item.reference_id,
                "description": item.description,
                "created_at": item.created_at.isoformat(),
            }
            for item in transactions
        ]
    }


@app.post("/admin/models", dependencies=[Depends(require_operator)])
def create_model(payload: ModelCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    preset = get_provider_preset(payload.provider_preset_id) if payload.provider_preset_id else None
    if payload.provider_preset_id and not preset:
        raise HTTPException(status_code=422, detail="provider preset not found")
    preset_model = preset.get_model(payload.upstream_model) if preset else None
    if preset and not preset_model:
        raise HTTPException(status_code=422, detail="该模型尚未纳入已核验的服务商目录，不能自动带入参数")
    public_name = preset_model.public_name if preset_model else payload.public_name
    if not public_name:
        raise HTTPException(status_code=422, detail="custom model requires public_name")
    if db.scalar(select(ModelConfig).where(ModelConfig.public_name == public_name)):
        raise HTTPException(status_code=409, detail="model already exists")
    connection = db.scalar(select(ProviderConnection).where(ProviderConnection.preset_id == preset.id)) if preset else None
    provider_name = str(preset.models[0].catalog_metadata.get("provider") or preset.name) if preset and preset.models else None
    provider_base_url = payload.provider_base_url or (connection.provider_base_url if connection else preset.base_url if preset else settings.default_provider_base_url)
    provider_api_key_env = payload.provider_api_key_env or (connection.provider_api_key_env if connection else preset.api_key_env if preset else None)
    catalog_metadata = dict(preset_model.catalog_metadata) if preset_model else {
        "display_name": payload.public_name,
        "provider": provider_name,
        "summary": f"通过 {provider_name} 服务商连接手工接入的模型。" if provider_name else "手工接入的自定义模型。",
        "modalities": ["text"],
        "capabilities": ["对话"],
        "supported_parameters": ["stream", "temperature", "max_tokens"],
        "context_window": "按上游配置",
        "model_version": "",
        "api_type": "chat_completions",
    }
    record = ModelConfig(
        public_name=public_name,
        upstream_model=preset_model.model_id if preset_model else payload.upstream_model,
        provider_base_url=provider_base_url,
        provider_api_key_env=provider_api_key_env,
        input_price_micros_per_1k=preset_model.platform_input_price_micros_per_1k if preset_model else payload.input_price_micros_per_1k,
        output_price_micros_per_1k=preset_model.platform_output_price_micros_per_1k if preset_model else payload.output_price_micros_per_1k,
        catalog_metadata_json=json.dumps(catalog_metadata, ensure_ascii=False),
        official_pricing_json=json.dumps(preset_model.official_pricing, ensure_ascii=False) if preset_model and preset_model.official_pricing else None,
    )
    db.add(record)
    db.flush()
    channel = ModelChannel(
        model_config_id=record.id,
        provider_connection_id=connection.id if connection else None,
        name="Primary",
        upstream_model=record.upstream_model,
        provider_base_url=record.provider_base_url,
        provider_api_key_env=record.provider_api_key_env,
        priority=100,
        weight=100,
    )
    if payload.provider_api_key:
        try:
            channel.encrypted_api_key = encrypt_provider_secret(payload.provider_api_key)
        except ProviderSecretError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    elif connection and connection.encrypted_api_key:
        channel.encrypted_api_key = connection.encrypted_api_key
    db.add(channel)
    db.commit()
    db.refresh(record)
    return {"id": record.id, "public_name": record.public_name, "upstream_model": record.upstream_model}


@app.get("/admin/provider-presets", dependencies=[Depends(require_admin)])
def list_provider_presets() -> dict[str, object]:
    return {"data": [provider_preset_data(item) for item in PROVIDER_PRESETS]}


def provider_callable_model_count(db: Session, connection: ProviderConnection | None, preset) -> int:
    if not connection:
        return 0
    linked_model_ids = set(db.scalars(
        select(ModelChannel.model_config_id).where(ModelChannel.provider_connection_id == connection.id)
    ).all())
    preset_public_names = {item.public_name for item in preset.models}
    models = [
        model for model in db.scalars(select(ModelConfig)).all()
        if model.id in linked_model_ids or model.public_name in preset_public_names
    ]
    return sum(1 for model in models if model_is_callable(db, model))


def provider_connection_data(db: Session, connection: ProviderConnection | None, preset) -> dict[str, object]:
    env_name = connection.provider_api_key_env if connection else preset.api_key_env
    env_configured = bool(env_name and os.getenv(env_name, "").strip())
    stored_secret = bool(connection and connection.encrypted_api_key)
    return {
        "id": connection.id if connection else None,
        "preset_id": preset.id,
        "name": preset.name,
        "provider_base_url": connection.provider_base_url if connection else preset.base_url,
        "provider_api_key_env": env_name,
        "credentials_configured": stored_secret or env_configured,
        "credential_source": "stored" if stored_secret else "environment" if env_configured else "none",
        "active": connection.active if connection else False,
        "status": connection.status if connection else "unconfigured",
        "discovered_model_count": connection.discovered_model_count if connection else 0,
        "synced_model_count": connection.synced_model_count if connection else 0,
        # This count changes when a model is published, disabled, or loses a
        # healthy channel, so the last provider-sync snapshot is not reliable.
        "callable_model_count": provider_callable_model_count(db, connection, preset),
        "default_input_price_micros_per_1k": connection.default_input_price_micros_per_1k if connection else 0,
        "default_output_price_micros_per_1k": connection.default_output_price_micros_per_1k if connection else 0,
        "last_checked_at": connection.last_checked_at.isoformat() if connection and connection.last_checked_at else None,
        "last_error": connection.last_error if connection else None,
        "balance_micros": connection.balance_micros if connection else None,
        "balance_currency": connection.balance_currency if connection else None,
        "balance_status": connection.balance_status if connection else "unknown",
        "balance_source": connection.balance_source if connection else None,
        "balance_checked_at": connection.balance_checked_at.isoformat() if connection and connection.balance_checked_at else None,
        "balance_error": connection.balance_error if connection else None,
        "balance_alert_threshold_micros": connection.balance_alert_threshold_micros if connection else 0,
        "model_count": len(preset.models),
        "note": preset.note,
    }


def mark_provider_connection_misconfigured(db: Session, connection: ProviderConnection | None, detail: str) -> None:
    if not connection:
        return
    checked_at = utcnow()
    connection.status = "misconfigured"
    connection.last_checked_at = checked_at
    connection.last_error = detail
    channels = db.scalars(select(ModelChannel).where(ModelChannel.provider_connection_id == connection.id)).all()
    for channel in channels:
        channel.status = "misconfigured"
        channel.health_source = "provider"
        channel.consecutive_failures = 0
        channel.circuit_open_until = None
        channel.last_checked_at = checked_at
        channel.last_error = detail
    db.commit()


@app.get("/admin/provider-connections", dependencies=[Depends(require_admin)])
def list_provider_connections(db: Session = Depends(get_db)) -> dict[str, object]:
    connections = {item.preset_id: item for item in db.scalars(select(ProviderConnection)).all()}
    return {"data": [provider_connection_data(db, connections.get(preset.id), preset) for preset in PROVIDER_PRESETS]}


@app.post("/admin/provider-connections/{preset_id}/test", dependencies=[Depends(require_operator)])
async def test_provider_connection(preset_id: str, payload: ProviderConnectionConfigure, db: Session = Depends(get_db)) -> dict[str, object]:
    preset = get_provider_preset(preset_id)
    connection = db.scalar(select(ProviderConnection).where(ProviderConnection.preset_id == preset_id))
    if not preset:
        raise HTTPException(status_code=404, detail="provider preset not found")
    base_url = payload.provider_base_url or (connection.provider_base_url if connection else preset.base_url)
    env_name = payload.provider_api_key_env if "provider_api_key_env" in payload.model_fields_set else (connection.provider_api_key_env if connection else preset.api_key_env)
    raw_secret = payload.provider_api_key
    if not raw_secret and connection and connection.encrypted_api_key:
        try:
            raw_secret = decrypt_provider_secret(connection.encrypted_api_key)
        except ProviderSecretError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    started = time.perf_counter()
    try:
        model_ids = await discover_upstream_models(base_url, env_name, raw_secret)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not provider_catalogue_matches(preset.id, model_ids):
        sample = ", ".join(model_ids[:6])
        detail = f"服务商目录与 {preset.name} 不匹配，请检查 API 地址和 API Key；返回样例: {sample}"
        mark_provider_connection_misconfigured(db, connection, detail)
        raise HTTPException(status_code=422, detail=detail)
    return {"ok": True, "provider": preset.name, "discovered_model_count": len(model_ids), "latency_ms": int((time.perf_counter() - started) * 1000), "sample_models": model_ids[:8]}


def _record_provider_balance(db: Session, connection: ProviderConnection, result: dict[str, object], note: str | None = None) -> None:
    status = str(result.get("status") or "error")
    connection.balance_status = status
    connection.balance_source = str(result.get("source") or "manual")
    connection.balance_checked_at = utcnow()
    connection.balance_error = str(result.get("detail") or "") or None
    if result.get("amount_micros") is not None:
        connection.balance_micros = int(result["amount_micros"])
        connection.balance_currency = str(result.get("currency") or "CNY").upper()
    db.add(ProviderBalanceSnapshot(
        provider_connection_id=connection.id,
        amount_micros=connection.balance_micros,
        currency=connection.balance_currency,
        status=status,
        source=connection.balance_source,
        raw_json=json.dumps(result.get("raw") or {"note": note} if isinstance(result.get("raw") or {"note": note}, dict) else {}, ensure_ascii=False, separators=(",", ":")),
        error_message=connection.balance_error,
        checked_at=connection.balance_checked_at,
    ))


@app.post("/admin/provider-connections/{preset_id}/balance/refresh", dependencies=[Depends(require_operator)])
async def refresh_provider_balance(preset_id: str, context: AdminContext = Depends(require_operator), db: Session = Depends(get_db)) -> dict[str, object]:
    preset = get_provider_preset(preset_id)
    connection = db.scalar(select(ProviderConnection).where(ProviderConnection.preset_id == preset_id))
    if not preset or not connection:
        raise HTTPException(status_code=404, detail="provider connection not found")
    raw_secret = None
    if connection.encrypted_api_key:
        try:
            raw_secret = decrypt_provider_secret(connection.encrypted_api_key)
        except ProviderSecretError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        result = await fetch_provider_balance(preset_id, connection.provider_base_url, connection.provider_api_key_env, raw_secret)
    except ValueError as exc:
        result = {"status": "error", "source": "api", "detail": str(exc)}
    _record_provider_balance(db, connection, result)
    record_audit_event(db, actor_type="admin", actor_id=context.actor_id, action="provider.balance_refreshed", target_type="provider_connection", target_id=connection.id, details={"preset_id": preset_id, "status": result.get("status"), "source": result.get("source")})
    db.commit()
    db.refresh(connection)
    return {"connection": provider_connection_data(db, connection, preset)}


@app.post("/admin/provider-connections/{preset_id}/balance/manual", dependencies=[Depends(require_operator)])
def record_manual_provider_balance(preset_id: str, payload: ProviderBalanceManual, context: AdminContext = Depends(require_operator), db: Session = Depends(get_db)) -> dict[str, object]:
    preset = get_provider_preset(preset_id)
    connection = db.scalar(select(ProviderConnection).where(ProviderConnection.preset_id == preset_id))
    if not preset or not connection:
        raise HTTPException(status_code=404, detail="provider connection not found")
    result = {"status": "available", "source": "manual", "amount_micros": round(payload.amount * 1_000_000), "currency": payload.currency.upper(), "raw": {"note": payload.note} if payload.note else {}}
    _record_provider_balance(db, connection, result, payload.note)
    record_audit_event(db, actor_type="admin", actor_id=context.actor_id, action="provider.balance_recorded", target_type="provider_connection", target_id=connection.id, details={"preset_id": preset_id, "currency": payload.currency.upper()})
    db.commit()
    db.refresh(connection)
    return {"connection": provider_connection_data(db, connection, preset)}


@app.put("/admin/provider-connections/{preset_id}", dependencies=[Depends(require_operator)])
async def configure_provider_connection(
    preset_id: str,
    payload: ProviderConnectionConfigure,
    context: AdminContext = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    preset = get_provider_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="provider preset not found")
    selected_ids = list(payload.model_ids or preset.model_ids)
    invalid = sorted(set(selected_ids) - set(preset.model_ids))
    if invalid:
        raise HTTPException(status_code=422, detail=f"model is not included in provider preset: {invalid[0]}")
    connection = db.scalar(select(ProviderConnection).where(ProviderConnection.preset_id == preset.id))
    base_url = payload.provider_base_url or (connection.provider_base_url if connection else preset.base_url)
    env_name = payload.provider_api_key_env if "provider_api_key_env" in payload.model_fields_set else (connection.provider_api_key_env if connection else preset.api_key_env)
    encrypted_secret = None if payload.clear_provider_api_key else (connection.encrypted_api_key if connection else None)
    if not encrypted_secret and not payload.clear_provider_api_key:
        # Migrate the first existing model-scoped secret into the provider-level
        # connection so current single-model deployments upgrade seamlessly.
        legacy_channel = db.scalar(select(ModelChannel).where(
            ModelChannel.provider_api_key_env == env_name,
            ModelChannel.encrypted_api_key.is_not(None),
        ).order_by(ModelChannel.id))
        if legacy_channel:
            encrypted_secret = legacy_channel.encrypted_api_key
    if payload.provider_api_key:
        try:
            encrypted_secret = encrypt_provider_secret(payload.provider_api_key)
        except ProviderSecretError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    raw_secret = payload.provider_api_key
    if not raw_secret and encrypted_secret:
        try:
            raw_secret = decrypt_provider_secret(encrypted_secret)
        except ProviderSecretError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not raw_secret and not (env_name and os.getenv(env_name, "").strip()):
        raise HTTPException(status_code=422, detail="请填写供应商 API Key，或先在服务环境中配置对应密钥变量")
    try:
        discovered_ids = set(await discover_upstream_models(base_url, env_name, raw_secret))
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not provider_catalogue_matches(preset.id, discovered_ids):
        sample = ", ".join(sorted(discovered_ids)[:6])
        detail = f"服务商目录与 {preset.name} 不匹配，请检查 API 地址和 API Key；返回样例: {sample}"
        mark_provider_connection_misconfigured(db, connection, detail)
        raise HTTPException(status_code=422, detail=detail)
    if connection is None:
        connection = ProviderConnection(
            preset_id=preset.id,
            name=preset.name,
            provider_base_url=base_url,
        )
        db.add(connection)
        db.flush()
    default_input_price = (
        payload.default_input_price_micros_per_1k
        if "default_input_price_micros_per_1k" in payload.model_fields_set
        else connection.default_input_price_micros_per_1k
    )
    default_output_price = (
        payload.default_output_price_micros_per_1k
        if "default_output_price_micros_per_1k" in payload.model_fields_set
        else connection.default_output_price_micros_per_1k
    )
    connection.name = preset.name
    connection.provider_base_url = base_url
    connection.provider_api_key_env = env_name
    connection.encrypted_api_key = encrypted_secret
    if "default_input_price_micros_per_1k" in payload.model_fields_set:
        connection.default_input_price_micros_per_1k = default_input_price
    if "default_output_price_micros_per_1k" in payload.model_fields_set:
        connection.default_output_price_micros_per_1k = default_output_price
    connection.balance_alert_threshold_micros = payload.balance_alert_threshold_micros
    connection.active = True
    connection.discovered_model_count = len(discovered_ids)
    connection.last_checked_at = utcnow()
    connection.updated_at = utcnow()
    connection.last_error = None
    # Older API clients used the legacy default-price fields as an implicit
    # request to publish callable text models. Preserve that contract only
    # for those explicit legacy payloads; the current UI never auto-publishes.
    legacy_auto_publish = (
        "auto_publish" not in payload.model_fields_set
        and (
            "default_input_price_micros_per_1k" in payload.model_fields_set
            or "default_output_price_micros_per_1k" in payload.model_fields_set
        )
    )
    should_auto_publish = payload.auto_publish or legacy_auto_publish
    synced: list[dict[str, object]] = []
    for model_id in selected_ids:
        preset_model = preset.get_model(model_id)
        if preset_model is None:
            continue
        model = db.scalar(select(ModelConfig).where(ModelConfig.public_name == preset_model.public_name))
        if model is None:
            model = ModelConfig(
                public_name=preset_model.public_name,
                upstream_model=preset_model.model_id,
                provider_base_url=base_url,
                provider_api_key_env=env_name,
                input_price_micros_per_1k=preset_model.platform_input_price_micros_per_1k or default_input_price,
                output_price_micros_per_1k=preset_model.platform_output_price_micros_per_1k or default_output_price,
                task_price_micros=preset_model.platform_task_price_micros,
                catalog_metadata_json=json.dumps(preset_model.catalog_metadata, ensure_ascii=False),
                official_pricing_json=json.dumps(preset_model.official_pricing, ensure_ascii=False) if preset_model.official_pricing else None,
                active=False,
            )
            db.add(model)
            db.flush()
        else:
            model.upstream_model = preset_model.model_id
            model.provider_base_url = base_url
            model.provider_api_key_env = env_name
            model.catalog_metadata_json = json.dumps(preset_model.catalog_metadata, ensure_ascii=False)
            if preset_model.official_pricing:
                model.official_pricing_json = json.dumps(preset_model.official_pricing, ensure_ascii=False)
            if model.input_price_micros_per_1k <= 0:
                model.input_price_micros_per_1k = preset_model.platform_input_price_micros_per_1k or default_input_price
            if model.output_price_micros_per_1k <= 0:
                model.output_price_micros_per_1k = preset_model.platform_output_price_micros_per_1k or default_output_price
            if model.task_price_micros <= 0:
                model.task_price_micros = preset_model.platform_task_price_micros
        channel = db.scalar(select(ModelChannel).where(
            ModelChannel.model_config_id == model.id,
            ModelChannel.provider_connection_id == connection.id,
        ))
        if channel is None:
            channel = db.scalar(select(ModelChannel).where(
                ModelChannel.model_config_id == model.id,
                ModelChannel.upstream_model == preset_model.model_id,
            ).order_by(ModelChannel.id))
        if channel is None:
            channel = ModelChannel(
                model_config_id=model.id,
                name=f"{preset.name} 主渠道",
                provider_base_url=base_url,
                upstream_model=preset_model.model_id,
            )
            db.add(channel)
        channel.provider_connection_id = connection.id
        channel.name = f"{preset.name} 主渠道"
        channel.provider_base_url = base_url
        channel.upstream_model = preset_model.model_id
        channel.provider_api_key_env = env_name
        channel.encrypted_api_key = encrypted_secret
        if not channel.provider_input_cost_micros_per_1k:
            channel.provider_input_cost_micros_per_1k = preset_model.platform_input_price_micros_per_1k
        if not channel.provider_output_cost_micros_per_1k:
            channel.provider_output_cost_micros_per_1k = preset_model.platform_output_price_micros_per_1k
        if not channel.provider_task_cost_micros and preset_model.provider_task_cost_micros:
            channel.provider_task_cost_micros = preset_model.provider_task_cost_micros
        available = preset_model.model_id in discovered_ids
        metadata_api_type = preset_model.catalog_metadata.get("api_type", "chat_completions")
        task_adapter_ready = metadata_api_type in {"images_generations", "video_generations"}
        channel.active = available and (metadata_api_type == "chat_completions" or task_adapter_ready)
        channel.status = "healthy" if channel.active else "unavailable"
        channel.health_source = "provider"
        channel.consecutive_failures = 0 if available else channel.consecutive_failures
        channel.last_checked_at = connection.last_checked_at
        channel.last_error = (
            None if available and (metadata_api_type == "chat_completions" or task_adapter_ready)
            else f"当前服务商账号尚未开放上游模型: {preset_model.model_id}"
        )
        price_ready = model.input_price_micros_per_1k > 0 and model.output_price_micros_per_1k > 0 if metadata_api_type == "chat_completions" else model.task_price_micros > 0
        callable_now = channel.active and price_ready
        if not available:
            model.active = False
        elif should_auto_publish and callable_now:
            model.active = True
        synced.append({
            "id": model.id,
            "public_name": model.public_name,
            "upstream_model": model.upstream_model,
            "input_price_micros_per_1k": model.input_price_micros_per_1k,
            "output_price_micros_per_1k": model.output_price_micros_per_1k,
            "task_price_micros": model.task_price_micros,
            "pricing_margin_bps": model.pricing_margin_bps,
            "available": available,
            "callable": bool(model.active and callable_now),
            "reason": None if callable_now else "请配置任务价格" if metadata_api_type != "chat_completions" and not price_ready else "请配置平台价格" if not price_ready else "尚未发布",
        })
    connection.synced_model_count = len(synced)
    connection.callable_model_count = sum(1 for item in synced if item["callable"])
    connection.status = "healthy" if connection.callable_model_count else "degraded"
    if connection.callable_model_count < len(synced):
        connection.last_error = f"{len(synced) - connection.callable_model_count} 个模型尚未达到可调用条件"
    record_audit_event(
        db,
        actor_type="admin",
        actor_id=context.actor_id,
        action="provider.connection_synced",
        target_type="provider_connection",
        target_id=connection.id,
        details={
            "preset_id": preset.id,
            "discovered": len(discovered_ids),
            "synced": len(synced),
            "callable": connection.callable_model_count,
        },
    )
    db.commit()
    db.refresh(connection)
    return {"connection": provider_connection_data(db, connection, preset), "models": synced}


@app.post("/admin/provider-presets/{preset_id}/install", dependencies=[Depends(require_operator)])
def install_provider_preset(preset_id: str, payload: ProviderPresetInstall, db: Session = Depends(get_db)) -> dict[str, object]:
    preset = get_provider_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="provider preset not found")
    invalid = sorted(set(payload.model_ids) - set(preset.model_ids))
    if invalid:
        raise HTTPException(status_code=422, detail=f"model is not included in provider preset: {invalid[0]}")
    preset_models = [preset.get_model(model_id) for model_id in payload.model_ids]
    if any(model is None for model in preset_models):
        raise HTTPException(status_code=422, detail="model is not included in provider preset")
    selected_models = [model for model in preset_models if model is not None]
    public_names = [model.public_name for model in selected_models]
    existing = set(db.scalars(select(ModelConfig.public_name).where(ModelConfig.public_name.in_(public_names))).all())
    if existing:
        raise HTTPException(status_code=409, detail=f"model already exists: {sorted(existing)[0]}")
    created: list[ModelConfig] = []
    for preset_model in selected_models:
        record = ModelConfig(
            public_name=preset_model.public_name,
            upstream_model=preset_model.model_id,
            provider_base_url=preset.base_url,
            provider_api_key_env=preset.api_key_env,
            input_price_micros_per_1k=preset_model.platform_input_price_micros_per_1k,
            output_price_micros_per_1k=preset_model.platform_output_price_micros_per_1k,
            catalog_metadata_json=json.dumps(preset_model.catalog_metadata, ensure_ascii=False),
            official_pricing_json=json.dumps(preset_model.official_pricing, ensure_ascii=False) if preset_model.official_pricing else None,
            active=False,
        )
        db.add(record)
        db.flush()
        db.add(ModelChannel(
            model_config_id=record.id,
            name=preset.name,
            upstream_model=preset_model.model_id,
            provider_base_url=preset.base_url,
            provider_api_key_env=preset.api_key_env,
            active=False,
        ))
        created.append(record)
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="model.provider_preset_installed", target_type="provider_preset", target_id=preset.id, details={"models": payload.model_ids})
    db.commit()
    return {"preset": provider_preset_data(preset), "data": [{"id": item.id, "public_name": item.public_name, "active": item.active} for item in created]}


@app.get("/admin/upstream-models", dependencies=[Depends(require_admin)])
async def list_upstream_models(
    provider_base_url: str,
    provider_api_key_env: str | None = None,
) -> dict[str, object]:
    try:
        model_ids = await discover_upstream_models(provider_base_url, provider_api_key_env)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"data": [{"id": model_id} for model_id in model_ids]}


@app.post("/admin/models/batch", dependencies=[Depends(require_operator)])
def import_models(payload: ModelBatchImport, db: Session = Depends(get_db)) -> dict[str, object]:
    names = [item.public_name for item in payload.models]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=422, detail="public model names must be unique in one import")
    existing = set(db.scalars(select(ModelConfig.public_name).where(ModelConfig.public_name.in_(names))).all())
    if existing:
        raise HTTPException(status_code=409, detail=f"model already exists: {sorted(existing)[0]}")
    created: list[ModelConfig] = []
    for item in payload.models:
        record = ModelConfig(
            public_name=item.public_name,
            upstream_model=item.upstream_model,
            provider_base_url=item.provider_base_url or payload.provider_base_url,
            provider_api_key_env=item.provider_api_key_env if item.provider_api_key_env is not None else payload.provider_api_key_env,
            input_price_micros_per_1k=item.input_price_micros_per_1k,
            output_price_micros_per_1k=item.output_price_micros_per_1k,
        )
        db.add(record)
        db.flush()
        db.add(ModelChannel(
            model_config_id=record.id,
            name="Primary",
            upstream_model=record.upstream_model,
            provider_base_url=record.provider_base_url,
            provider_api_key_env=record.provider_api_key_env,
            priority=100,
            weight=100,
        ))
        created.append(record)
    record_audit_event(
        db, actor_type="admin", actor_id="token-admin", action="model.batch_imported",
        target_type="model_batch", target_id=created[0].id,
        details={"count": len(created), "models": [item.public_name for item in created]},
    )
    db.commit()
    return {"data": [{"id": item.id, "public_name": item.public_name, "upstream_model": item.upstream_model} for item in created]}


@app.get("/admin/models", dependencies=[Depends(require_admin)])
def list_admin_models(db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    models = db.scalars(
        select(ModelConfig)
        .where(ModelConfig.public_name.not_in(DEPRECATED_PROVIDER_MODEL_PUBLIC_NAMES))
        .order_by(ModelConfig.id.desc())
    ).all()
    return {"data": [
        {
            "id": model.id,
            "public_name": model.public_name,
            "upstream_model": model.upstream_model,
            "provider_base_url": model.provider_base_url,
            "provider_api_key_env": model.provider_api_key_env,
            "input_price_micros_per_1k": model.input_price_micros_per_1k,
            "output_price_micros_per_1k": model.output_price_micros_per_1k,
            "pricing_margin_bps": model.pricing_margin_bps,
            "catalog_metadata": parse_model_json(model.catalog_metadata_json),
            "official_pricing": parse_model_json(model.official_pricing_json),
            "active": model.active,
            "publication_state": publication_state,
            "publication_reasons": publication_reasons,
            "mock_mode": settings.mock_mode,
            "channel_count": db.scalar(select(func.count(ModelChannel.id)).where(ModelChannel.model_config_id == model.id)) or 0,
            "healthy_channel_count": db.scalar(select(func.count(ModelChannel.id)).where(
                ModelChannel.model_config_id == model.id,
                ModelChannel.active.is_(True),
                ModelChannel.status == "healthy",
            )) or 0,
            "created_at": model.created_at.isoformat(),
        }
        for model in models
        for channels in [db.scalars(select(ModelChannel).where(ModelChannel.model_config_id == model.id)).all()]
        for publication_state, publication_reasons in [_model_publication_state(model, channels, settings)]
    ]}


@app.patch("/admin/models/{model_id}", dependencies=[Depends(require_operator)])
def update_model(model_id: int, payload: ModelUpdate, db: Session = Depends(get_db)) -> dict[str, object]:
    model = db.get(ModelConfig, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="model not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="no model changes provided")
    if "pricing_margin_bps" in changes and changes["pricing_margin_bps"]:
        input_price, output_price = prices_from_margin(model, changes["pricing_margin_bps"])
        changes["input_price_micros_per_1k"] = input_price
        changes["output_price_micros_per_1k"] = output_price
    elif "pricing_margin_bps" not in changes and ({"input_price_micros_per_1k", "output_price_micros_per_1k"} & changes.keys()):
        # A direct price edit is an explicit manual override of the margin strategy.
        changes["pricing_margin_bps"] = 0
    if changes.get("active"):
        catalog_metadata = parse_model_json(model.catalog_metadata_json) or {}
        api_type = catalog_metadata.get("api_type", "chat_completions")
        settings = get_settings()
        input_price = changes.get("input_price_micros_per_1k", model.input_price_micros_per_1k)
        output_price = changes.get("output_price_micros_per_1k", model.output_price_micros_per_1k)
        task_price = changes.get("task_price_micros", model.task_price_micros)
        if api_type == "chat_completions" and (input_price <= 0 or output_price <= 0):
            raise HTTPException(status_code=422, detail="configure positive platform input and output prices before publishing a model")
        if api_type in {"images_generations", "video_generations"} and task_price <= 0:
            raise HTTPException(status_code=422, detail="发布任务模型前需配置大于 0 的单次生成价格")
        channels = db.scalars(select(ModelChannel).where(ModelChannel.model_config_id == model.id)).all()
        if settings.mock_mode:
            healthy_channel = any(channel.active and channel.status == "healthy" for channel in channels)
        else:
            healthy_channel = any(
                channel.active and channel.status == "healthy" and channel.health_source == "provider"
                and _channel_credentials_configured(channel)
                for channel in channels
            )
        if not healthy_channel:
            raise HTTPException(status_code=422, detail="run a successful real provider health check with configured credentials before publishing a model")
    for field, value in changes.items():
        setattr(model, field, value)
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="model.updated", target_type="model", target_id=model.id, details=changes)
    db.commit()
    return {
        "id": model.id,
        "active": model.active,
        "input_price_micros_per_1k": model.input_price_micros_per_1k,
        "output_price_micros_per_1k": model.output_price_micros_per_1k,
        "pricing_margin_bps": model.pricing_margin_bps,
    }


@app.delete("/admin/models/{model_id}", dependencies=[Depends(require_superadmin)])
def delete_model(model_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    model = db.get(ModelConfig, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="model not found")
    if model.active:
        raise HTTPException(status_code=409, detail="请先下架模型后再删除")
    usage_count = db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.model == model.public_name)) or 0
    if usage_count:
        raise HTTPException(status_code=409, detail="该模型已有调用记录，不能删除；请保留下架状态以便审计")
    model_id_value = model.id
    public_name = model.public_name
    db.execute(delete(ModelChannel).where(ModelChannel.model_config_id == model.id))
    db.delete(model)
    record_audit_event(db, actor_type="admin", actor_id="token-admin", action="model.deleted", target_type="model", target_id=model_id_value, details={"public_name": public_name})
    db.commit()
    return {"id": model_id_value, "public_name": public_name, "deleted": True}


def parse_model_json(value: str | None) -> dict[str, object] | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def official_reference_prices(model: ModelConfig) -> tuple[int, int] | None:
    """Return official input/output costs in the ledger's /1K micros unit."""
    pricing = parse_model_json(model.official_pricing_json) or {}
    default_reference = pricing.get("default_reference")
    if isinstance(default_reference, dict):
        input_per_million = default_reference.get("input_micros")
        output_per_million = default_reference.get("output_micros")
        if isinstance(input_per_million, (int, float)) and isinstance(output_per_million, (int, float)):
            if input_per_million > 0 and output_per_million > 0:
                return round(input_per_million / 1000), round(output_per_million / 1000)
    off_peak = pricing.get("off_peak")
    if not isinstance(off_peak, dict):
        return None
    input_per_million = off_peak.get("input_cache_miss_micros")
    output_per_million = off_peak.get("output_micros")
    if not isinstance(input_per_million, (int, float)) or not isinstance(output_per_million, (int, float)):
        return None
    if input_per_million <= 0 or output_per_million <= 0:
        return None
    return round(input_per_million / 1000), round(output_per_million / 1000)


def prices_from_margin(model: ModelConfig, margin_bps: int) -> tuple[int, int]:
    reference = official_reference_prices(model)
    if not reference:
        raise HTTPException(status_code=422, detail="该模型没有可核验的官方价格，无法按利润率自动定价")
    denominator = 10_000 - margin_bps
    return tuple((price * 10_000 + denominator - 1) // denominator for price in reference)


def channel_data(channel: ModelChannel) -> dict[str, object]:
    credential_source = "console" if channel.encrypted_api_key else "environment" if channel.provider_api_key_env else "default"
    return {
        "id": channel.id,
        "model_config_id": channel.model_config_id,
        "provider_connection_id": channel.provider_connection_id,
        "name": channel.name,
        "provider_base_url": channel.provider_base_url,
        "upstream_model": channel.upstream_model,
        "provider_api_key_env": channel.provider_api_key_env,
        "credentials_configured": _channel_credentials_configured(channel),
        "credential_source": credential_source,
        "health_source": channel.health_source,
        "priority": channel.priority,
        "weight": channel.weight,
        "active": channel.active,
        "status": channel.status,
        "consecutive_failures": channel.consecutive_failures,
        "circuit_open_until": channel.circuit_open_until.isoformat() if channel.circuit_open_until else None,
        "last_checked_at": channel.last_checked_at.isoformat() if channel.last_checked_at else None,
        "last_error": channel.last_error,
        "provider_input_cost_micros_per_1k": channel.provider_input_cost_micros_per_1k,
        "provider_output_cost_micros_per_1k": channel.provider_output_cost_micros_per_1k,
        "created_at": channel.created_at.isoformat(),
    }


@app.get("/admin/models/{model_id}/channels", dependencies=[Depends(require_admin)])
def list_model_channels(model_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    if not db.get(ModelConfig, model_id):
        raise HTTPException(status_code=404, detail="model not found")
    channels = db.scalars(
        select(ModelChannel)
        .where(ModelChannel.model_config_id == model_id)
        .order_by(ModelChannel.priority, ModelChannel.id)
    ).all()
    return {"data": [channel_data(channel) for channel in channels]}


@app.post("/admin/models/{model_id}/channels", dependencies=[Depends(require_operator)])
def create_model_channel(model_id: int, payload: ModelChannelCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    if not db.get(ModelConfig, model_id):
        raise HTTPException(status_code=404, detail="model not found")
    if db.scalar(select(ModelChannel).where(
        ModelChannel.model_config_id == model_id,
        ModelChannel.name == payload.name,
    )):
        raise HTTPException(status_code=409, detail="channel name already exists for model")
    secret = payload.provider_api_key
    channel = ModelChannel(model_config_id=model_id, **payload.model_dump(exclude={"provider_api_key"}))
    if secret:
        try:
            channel.encrypted_api_key = encrypt_provider_secret(secret)
        except ProviderSecretError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel_data(channel)


@app.patch("/admin/channels/{channel_id}", dependencies=[Depends(require_operator)])
def update_model_channel(channel_id: int, payload: ModelChannelUpdate, db: Session = Depends(get_db)) -> dict[str, object]:
    channel = db.get(ModelChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="channel not found")
    secret = payload.provider_api_key
    clear_secret = payload.clear_provider_api_key
    changes = payload.model_dump(exclude_unset=True, exclude={"provider_api_key", "clear_provider_api_key"})
    if "name" in changes and db.scalar(select(ModelChannel).where(
        ModelChannel.model_config_id == channel.model_config_id,
        ModelChannel.name == changes["name"],
        ModelChannel.id != channel.id,
    )):
        raise HTTPException(status_code=409, detail="channel name already exists for model")
    for field, value in changes.items():
        setattr(channel, field, value)
    if clear_secret:
        channel.encrypted_api_key = None
    elif secret:
        try:
            channel.encrypted_api_key = encrypt_provider_secret(secret)
        except ProviderSecretError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.commit()
    db.refresh(channel)
    return channel_data(channel)


@app.post("/admin/channels/{channel_id}/check", dependencies=[Depends(require_operator)])
async def run_channel_health_check(channel_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    channel = db.get(ModelChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="channel not found")
    result = await check_channel_health(db, channel)
    db.refresh(channel)
    return {**channel_data(channel), **result}


@app.post("/admin/models/health-check", dependencies=[Depends(require_operator)])
async def run_all_channel_health_checks(provider_preset_id: str | None = None, db: Session = Depends(get_db)) -> dict[str, object]:
    channels = db.scalars(select(ModelChannel).where(ModelChannel.active.is_(True)).order_by(ModelChannel.priority, ModelChannel.id)).all()
    if provider_preset_id:
        preset = get_provider_preset(provider_preset_id)
        if not preset:
            raise HTTPException(status_code=422, detail="provider preset not found")
        provider_name = str(preset.models[0].catalog_metadata.get("provider") or preset.name) if preset.models else preset.name
        connection = db.scalar(select(ProviderConnection).where(ProviderConnection.preset_id == preset.id))
        model_providers = {
            model.id: (parse_model_json(model.catalog_metadata_json) or {}).get("provider")
            for model in db.scalars(select(ModelConfig)).all()
        }
        channels = [
            channel for channel in channels
            if (connection and channel.provider_connection_id == connection.id) or model_providers.get(channel.model_config_id) == provider_name
        ]
    results = []
    for channel in channels:
        result = await check_channel_health(db, channel)
        db.refresh(channel)
        results.append({"channel_id": channel.id, "model_config_id": channel.model_config_id, "status": channel.status, **result})
    return {
        "checked": len(results),
        "healthy": sum(1 for item in results if item["healthy"]),
        "unhealthy": sum(1 for item in results if item["status"] == "unhealthy"),
        "unavailable": sum(1 for item in results if item["status"] == "unavailable"),
        "pending_adapter": sum(1 for item in results if item["status"] == "pending_adapter"),
        "misconfigured": sum(1 for item in results if item["status"] == "misconfigured"),
        "data": results,
    }


@app.post("/admin/models/{model_id}/preflight", dependencies=[Depends(require_superadmin)])
async def preflight_model(
    model_id: int,
    payload: ModelPreflightRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Run explicit pre-release checks. Chat and stream probes may incur provider charges."""
    model = db.get(ModelConfig, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="model not found")
    channels = db.scalars(select(ModelChannel).where(ModelChannel.model_config_id == model.id, ModelChannel.active.is_(True))).all()
    health = []
    for channel in channels:
        result = await check_channel_health(db, channel)
        health.append({"channel_id": channel.id, "name": channel.name, **result})
    report: dict[str, object] = {
        "model": model.public_name,
        "price_configured": model.input_price_micros_per_1k > 0 and model.output_price_micros_per_1k > 0,
        "channel_health": health,
        "chat_probe": None,
        "stream_probe": None,
    }
    publication_state, publication_reasons = _model_publication_state(model, channels)
    report["publication_state"] = publication_state
    report["publication_reasons"] = publication_reasons
    report["ready_to_publish"] = publication_state in {"published", "mock_published", "candidate"} and not publication_reasons
    probe = ChatCompletionRequest(model=model.public_name, messages=[{"role": "user", "content": payload.prompt}], max_tokens=32)
    if payload.chat_probe:
        try:
            _, input_tokens, output_tokens = await call_provider(db, model, probe)
            report["chat_probe"] = {
                "ok": True,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_amount_micros": calculate_amount(model, input_tokens, output_tokens),
            }
        except Exception as exc:
            report["chat_probe"] = {"ok": False, "detail": str(exc)}
    if payload.stream_probe:
        stream_chunks = 0
        stream_usage: dict[str, int] | None = None
        try:
            streaming_probe = probe.model_copy(update={"stream": True})
            async for raw_chunk in stream_provider(db, model, streaming_probe):
                stream_chunks += 1
                text = raw_chunk.decode("utf-8", errors="replace").strip()
                if not text.startswith("data: "):
                    continue
                try:
                    event = json.loads(text[6:])
                    usage = event.get("usage") if isinstance(event, dict) else None
                    if isinstance(usage, dict) and "prompt_tokens" in usage and "completion_tokens" in usage:
                        stream_usage = {
                            "input_tokens": int(usage["prompt_tokens"]),
                            "output_tokens": int(usage["completion_tokens"]),
                        }
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            report["stream_probe"] = {
                "ok": stream_chunks > 0,
                "chunk_count": stream_chunks,
                "token_usage_reported": stream_usage is not None,
                **(stream_usage or {}),
                **({"estimated_amount_micros": calculate_amount(model, stream_usage["input_tokens"], stream_usage["output_tokens"])} if stream_usage else {}),
            }
        except Exception as exc:
            report["stream_probe"] = {"ok": False, "chunk_count": stream_chunks, "detail": str(exc)}
    return report


@app.get("/v1/models")
def list_models(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict[str, object]:
    require_api_key(authorization, db)
    models = [model for model in db.scalars(select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.public_name)).all() if model_is_callable(db, model)]
    data = []
    for item in models:
        metadata = parse_model_json(item.catalog_metadata_json) or {}
        data.append({
            "id": item.public_name,
            "object": "model",
            "owned_by": metadata.get("provider", "token"),
            "created": int(item.created_at.timestamp()),
            "context_length": metadata.get("context_window"),
            "architecture": {
                "input_modalities": metadata.get("modalities", ["text"]),
                "output_modalities": ["text"],
            },
            "pricing": {
                "input_cny_per_1k": item.input_price_micros_per_1k,
                "output_cny_per_1k": item.output_price_micros_per_1k,
                "task_cny": item.task_price_micros,
            },
            "supported_parameters": metadata.get("supported_parameters", ["messages", "stream", "temperature", "max_tokens"]),
            "gateway_profile": metadata.get("gateway_profile"),
            "max_output_tokens": metadata.get("max_output_tokens"),
        })
    return {"object": "list", "data": data}


@app.get("/v1/models/{model_id:path}")
def retrieve_model(model_id: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict[str, str]:
    require_api_key(authorization, db)
    model = db.scalar(select(ModelConfig).where(ModelConfig.public_name == model_id, ModelConfig.active.is_(True)))
    if not model:
        raise HTTPException(status_code=404, detail=f"unknown model: {model_id}")
    if not model_is_callable(db, model):
        raise HTTPException(status_code=503, detail=f"model unavailable: {model_id}")
    return {"id": model.public_name, "object": "model", "owned_by": "token"}


@app.get("/v1/account", response_model=AccountBalance)
def account_balance(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> AccountBalance:
    api_key = require_api_key(authorization, db)
    account = db.get(BillingAccount, api_key.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=403, detail="billing account is inactive")
    return AccountBalance(account_id=account.id, external_user_id=account.external_user_id, api_key_id=api_key.id, balance_micros=account.balance_micros)


def _generation_context(
    authorization: str | None,
    model_name: str,
    expected_api_type: str,
    db: Session,
) -> tuple[ApiKey, BillingAccount, ModelConfig]:
    api_key = require_api_key(authorization, db)
    settings = get_settings()
    rate_limiter.check("api", str(api_key.id), settings.api_rate_limit_requests, settings.api_rate_limit_window_seconds)
    account = db.get(BillingAccount, api_key.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=403, detail="billing account is inactive")
    model = db.scalar(select(ModelConfig).where(ModelConfig.public_name == model_name, ModelConfig.active.is_(True)))
    if not model:
        raise HTTPException(status_code=404, detail=f"unknown model: {model_name}")
    metadata = parse_model_json(model.catalog_metadata_json) or {}
    if metadata.get("api_type") != expected_api_type:
        raise HTTPException(status_code=422, detail=f"模型 {model_name} 不是当前生成协议可调用模型")
    if not model_is_callable(db, model):
        raise HTTPException(status_code=503, detail=f"model unavailable: {model_name}")
    if model.task_price_micros <= 0:
        raise HTTPException(status_code=503, detail=f"model pricing unavailable: {model_name}")
    return api_key, account, model


def _generation_response(task: GenerationTask, model: ModelConfig) -> dict[str, object]:
    result = parse_model_json(task.result_json) or {}
    return {
        "id": task.task_id,
        "object": "video.generation" if task.task_type == "video_generations" else "image.generation",
        "created": int(task.created_at.timestamp()),
        "model": model.public_name,
        "status": task.status,
        "data": result.get("data", []),
        "error": task.error_message,
    }


def _settle_generation_task(db: Session, task: GenerationTask, account: BillingAccount, api_key: ApiKey, model: ModelConfig, *, success: bool, provider_cost_micros: int = 0) -> None:
    if task.settled_at:
        return
    actual_amount = task.reserved_micros if success else 0
    settle_balance(db, account, api_key, task.reserved_micros, actual_amount, task.request_id)
    save_usage(
        db, api_key, model, task.request_id, task.trace_id, 0, 0,
        "success" if success else "error", 0, task.error_message,
        provider_cost_micros=provider_cost_micros,
        provider_channel_id=task.provider_channel_id,
        provider_request_id=task.provider_task_id,
        raw_usage={"task_id": task.task_id, "task_type": task.task_type, "quantity": task.quantity, "result": parse_model_json(task.result_json)},
        amount_micros=actual_amount,
    )
    task.settled_at = utcnow()
    db.commit()


async def _create_generation(
    payload: ImageGenerationRequest | VideoGenerationRequest,
    expected_api_type: str,
    authorization: str | None,
    x_request_id: str | None,
    x_trace_id: str | None,
    db: Session,
) -> JSONResponse:
    api_key, account, model = _generation_context(authorization, payload.model, expected_api_type, db)
    request_id = x_request_id or "req_" + uuid.uuid4().hex
    trace_id = x_trace_id or request_id
    if not _request_id_pattern.fullmatch(request_id) or not _request_id_pattern.fullmatch(trace_id):
        raise HTTPException(status_code=422, detail="request and trace IDs must be 1-64 URL-safe characters")
    if db.scalar(select(GenerationTask).where(GenerationTask.request_id == request_id)) or db.scalar(select(UsageRecord).where(UsageRecord.request_id == request_id)):
        raise HTTPException(status_code=409, detail="request id already used")
    quantity = int(getattr(payload, "n", 1) or 1)
    reservation = model.task_price_micros * quantity
    try:
        reserve_balance(db, account, api_key, reservation, request_id)
    except ValueError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    task = GenerationTask(
        task_id="task_" + uuid.uuid4().hex,
        account_id=account.id,
        api_key_id=api_key.id,
        model_config_id=model.id,
        request_id=request_id,
        trace_id=trace_id,
        task_type=expected_api_type,
        status="processing",
        quantity=quantity,
        reserved_micros=reservation,
    )
    db.add(task)
    db.commit()
    try:
        upstream_payload = payload.model_dump(exclude_none=True)
        detail = await create_provider_task(db, model, upstream_payload)
        task.provider_channel_id = detail.channel_id
        task.provider_task_id = detail.provider_task_id
        task.status = detail.status
        task.result_json = json.dumps(detail.result, ensure_ascii=False)
        task.updated_at = utcnow()
        db.commit()
        if detail.status in {"completed", "failed"}:
            task.error_message = None if detail.status == "completed" else "provider task failed"
            _settle_generation_task(db, task, account, api_key, model, success=detail.status == "completed", provider_cost_micros=detail.provider_cost_micros)
        response = _generation_response(task, model)
        return JSONResponse(response, status_code=200 if detail.status == "completed" else 202, headers={"X-Request-ID": request_id, "X-Trace-ID": trace_id})
    except HTTPException as exc:
        task.status = "failed"
        task.error_message = str(exc.detail)
        task.updated_at = utcnow()
        db.commit()
        _settle_generation_task(db, task, account, api_key, model, success=False)
        raise


@app.post("/v1/images/generations")
async def image_generations(
    payload: ImageGenerationRequest,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    return await _create_generation(payload, "images_generations", authorization, x_request_id, x_trace_id, db)


@app.post("/v1/videos/generations")
async def video_generations(
    payload: VideoGenerationRequest,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    return await _create_generation(payload, "video_generations", authorization, x_request_id, x_trace_id, db)


@app.get("/v1/generation-tasks/{task_id}")
async def generation_task(task_id: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict[str, object]:
    api_key = require_api_key(authorization, db)
    task = db.scalar(select(GenerationTask).where(GenerationTask.task_id == task_id, GenerationTask.api_key_id == api_key.id))
    if not task:
        raise HTTPException(status_code=404, detail="generation task not found")
    model = db.get(ModelConfig, task.model_config_id)
    account = db.get(BillingAccount, task.account_id)
    if not model or not account:
        raise HTTPException(status_code=404, detail="generation task model or account not found")
    if task.status == "processing":
        try:
            detail = await refresh_provider_task(db, task, model)
            task.status = detail.status
            task.provider_task_id = detail.provider_task_id
            task.result_json = json.dumps(detail.result, ensure_ascii=False)
            task.updated_at = utcnow()
            db.commit()
            if task.status in {"completed", "failed"}:
                task.error_message = None if task.status == "completed" else "provider task failed"
                _settle_generation_task(db, task, account, api_key, model, success=task.status == "completed", provider_cost_micros=detail.provider_cost_micros)
        except HTTPException as exc:
            task.status = "failed"
            task.error_message = str(exc.detail)
            task.updated_at = utcnow()
            db.commit()
            _settle_generation_task(db, task, account, api_key, model, success=False)
    return _generation_response(task, model)


@app.post("/v1/chat/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    api_key = require_api_key(authorization, db)
    settings = get_settings()
    rate_limiter.check("api", str(api_key.id), settings.api_rate_limit_requests, settings.api_rate_limit_window_seconds)
    account = db.get(BillingAccount, api_key.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=403, detail="billing account is inactive")
    model = db.scalar(select(ModelConfig).where(ModelConfig.public_name == payload.model, ModelConfig.active.is_(True)))
    if not model:
        raise HTTPException(status_code=404, detail=f"unknown model: {payload.model}")
    if not model_is_callable(db, model):
        raise HTTPException(status_code=503, detail=f"model unavailable: {payload.model}")
    validate_model_request(model, payload)
    request_id = x_request_id or "req_" + uuid.uuid4().hex
    trace_id = x_trace_id or request_id
    if not _request_id_pattern.fullmatch(request_id) or not _request_id_pattern.fullmatch(trace_id):
        raise HTTPException(status_code=422, detail="request and trace IDs must be 1-64 URL-safe characters")
    if db.scalar(select(UsageRecord).where(UsageRecord.request_id == request_id)):
        raise HTTPException(status_code=409, detail="request id already used")
    estimated_input = estimate_tokens(payload.messages)
    reservation = calculate_amount(model, estimated_input, payload.max_tokens or payload.max_completion_tokens or settings.reservation_output_tokens)
    try:
        reserve_balance(db, account, api_key, reservation, request_id)
    except ValueError as exc:
        detail = str(exc)
        save_usage(db, api_key, model, request_id, trace_id, estimated_input, 0, "rejected", 0, detail)
        raise HTTPException(status_code=402, detail=detail) from exc
    if payload.stream:
        async def event_stream():
            started = time.perf_counter()
            input_tokens = 0
            output_tokens = 0
            content_parts: list[str] = []
            saw_provider_data = False
            completed = False
            error_message: str | None = None
            route_meta: dict[str, object] = {}
            try:
                async for chunk in stream_provider(db, model, payload, route_meta):
                    text = chunk.decode("utf-8", errors="replace")
                    if text.startswith("data: ") and "[DONE]" not in text:
                        try:
                            data = json.loads(text[6:].strip())
                            usage = data.get("usage") or {}
                            if usage:
                                input_tokens = int(usage.get("prompt_tokens", input_tokens or estimated_input))
                                output_tokens = int(usage.get("completion_tokens", output_tokens))
                            for choice in data.get("choices") or []:
                                content = (choice.get("delta") or {}).get("content")
                                if isinstance(content, str):
                                    content_parts.append(content)
                                    saw_provider_data = True
                        except (TypeError, ValueError, json.JSONDecodeError):
                            pass
                    yield chunk
                completed = True
            except asyncio.CancelledError:
                error_message = "client disconnected during streaming response"
                raise
            except Exception as exc:
                error_message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                error_payload = {"error": {"message": "provider stream failed", "type": "provider_error"}}
                yield f"data: {json.dumps(error_payload, separators=(',', ':'))}\n\ndata: [DONE]\n\n".encode("utf-8")
            finally:
                if not output_tokens and content_parts:
                    output_tokens = max(1, len("".join(content_parts)) // 4)
                if not input_tokens and (completed or saw_provider_data):
                    input_tokens = estimated_input
                actual_amount = calculate_amount(model, input_tokens, output_tokens)
                provider_channel = db.get(ModelChannel, route_meta.get("provider_channel_id")) if route_meta.get("provider_channel_id") else None
                settle_balance(db, account, api_key, reservation, actual_amount, request_id)
                save_usage(
                    db,
                    api_key,
                    model,
                    request_id,
                    trace_id,
                    input_tokens,
                    output_tokens,
                    "success" if completed else "error",
                    int((time.perf_counter() - started) * 1000),
                    error_message,
                    provider_cost_micros=provider_cost(provider_channel, input_tokens, output_tokens, route_meta.get("usage_details")) if provider_channel else 0,
                    provider_channel_id=provider_channel.id if provider_channel else None,
                    provider_request_id=route_meta.get("provider_request_id"),
                    usage_details=route_meta.get("usage_details"),
                    raw_usage=route_meta.get("raw_usage"),
                    route_attempts=route_meta.get("route_attempts"),
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"X-Request-ID": request_id, "X-Trace-ID": trace_id, "Cache-Control": "no-cache"},
        )
    started = time.perf_counter()
    try:
        provider_result = await call_provider_details(db, model, payload)
        response = provider_result.response
        input_tokens = provider_result.input_tokens
        output_tokens = provider_result.output_tokens
        actual_amount = calculate_amount(model, input_tokens, output_tokens)
        settle_balance(db, account, api_key, reservation, actual_amount, request_id)
        save_usage(
            db, api_key, model, request_id, trace_id, input_tokens, output_tokens, "success",
            int((time.perf_counter() - started) * 1000),
            provider_cost_micros=provider_result.provider_cost_micros,
            provider_channel_id=provider_result.channel_id,
            provider_request_id=provider_result.provider_request_id,
            usage_details=provider_result.usage_details,
            raw_usage=provider_result.raw_usage,
            route_attempts=provider_result.route_attempts,
        )
        response.setdefault("model", model.public_name)
        return JSONResponse(response, headers={"X-Request-ID": request_id, "X-Trace-ID": trace_id})
    except HTTPException as exc:
        settle_balance(db, account, api_key, reservation, 0, request_id)
        save_usage(db, api_key, model, request_id, trace_id, 0, 0, "error", int((time.perf_counter() - started) * 1000), str(exc.detail))
        raise
    except Exception as exc:
        settle_balance(db, account, api_key, reservation, 0, request_id)
        save_usage(db, api_key, model, request_id, trace_id, 0, 0, "error", int((time.perf_counter() - started) * 1000), str(exc))
        raise HTTPException(status_code=502, detail="provider response could not be processed") from exc


@app.get("/admin/usage", response_model=UsageSummary, dependencies=[Depends(require_admin)])
def usage_summary(db: Session = Depends(get_db)) -> UsageSummary:
    count, inputs, outputs, total, amount = db.execute(
        select(
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        )
    ).one()
    return UsageSummary(request_count=count, input_tokens=inputs, output_tokens=outputs, total_tokens=total, amount_micros=amount)


@app.get("/admin/usage/records", dependencies=[Depends(require_admin)])
def usage_records(db: Session = Depends(get_db)) -> dict[str, object]:
    rows = db.execute(
        select(UsageRecord, BillingAccount.name, ApiKey.name)
        .join(BillingAccount, BillingAccount.id == UsageRecord.account_id)
        .join(ApiKey, ApiKey.id == UsageRecord.api_key_id)
        .order_by(UsageRecord.id.desc())
        .limit(100)
    ).all()
    return {"data": [
        {
            "id": record.id,
            "request_id": record.request_id,
            "trace_id": record.trace_id,
            "account_id": record.account_id,
            "account_name": account_name,
            "api_key_id": record.api_key_id,
            "api_key_name": api_key_name,
            "model": record.model,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "total_tokens": record.total_tokens,
            "amount_micros": record.amount_micros,
            "provider_cost_micros": record.provider_cost_micros,
            "provider_channel_id": record.provider_channel_id,
            "provider_request_id": record.provider_request_id,
            "input_cache_hit_tokens": record.input_cache_hit_tokens,
            "input_cache_miss_tokens": record.input_cache_miss_tokens,
            "reasoning_tokens": record.reasoning_tokens,
            "route_attempts": json.loads(record.route_attempts_json or "[]"),
            "status": record.status,
            "latency_ms": record.latency_ms,
            "error_message": record.error_message,
            "created_at": record.created_at.isoformat(),
        }
        for record, account_name, api_key_name in rows
    ]}


@app.get("/admin/audit-events", dependencies=[Depends(require_admin)])
def list_audit_events(db: Session = Depends(get_db)) -> dict[str, object]:
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(200)).all()
    return {"data": [
        {
            "id": event.id,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "action": event.action,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "details": json.loads(event.details_json) if event.details_json else {},
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]}
