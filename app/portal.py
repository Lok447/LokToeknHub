import csv
import base64
import hashlib
import hmac
import io
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import quote, urlencode, urlparse
import uuid

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import Date, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import record_audit_event
from .builtin_models import model_metadata
from .config import get_settings
from .db import get_db
from .guardrails import rate_limiter
from .model_release import model_is_callable
from .models import AccountBalanceTransaction, ApiKey, BillingAccount, ExternalIdentity, GenerationTask, ModelChannel, ModelConfig, OidcLoginChallenge, OrganizationMember, PasswordResetChallenge, PaymentOrder, RedemptionClaim, RedemptionCode, SecurityContactChallenge, SecurityNotification, UsageRecord, Workspace, utcnow
from .payment_providers import payment_providers, require_available_provider
from .schemas import ActiveUpdate, ChatCompletionRequest, OrganizationCreate, OrganizationMemberCreate, PasswordResetConfirm, PasswordResetRequest, PaymentOrderCreate, PortalApiKeyCreate, PortalLogin, PortalModelTestRequest, PortalRegister, ProjectCreate, RedemptionCodeRedeem, SecurityContactConfirm, SecurityContactUpdate, TrialLinkCreate
from .security import PortalContext, create_key, create_password_reset_token, create_portal_session_token, create_trial_token, hash_key, hash_password, require_operator, require_portal_context, verify_password
from .services import call_provider_details, calculate_amount, create_provider_task, estimate_tokens, reserve_balance, save_usage, settle_balance, validate_model_request
from .workspaces import accessible_workspaces, create_organization, create_project, ensure_default_project, ensure_personal_workspace, project_access, require_workspace_manager, workspace_access, workspace_data


router = APIRouter()


def record_security_notification(db: Session, account: BillingAccount, event_type: str, details: dict[str, object] | None = None) -> None:
    db.add(SecurityNotification(
        account_id=account.id,
        event_type=event_type,
        details_json=json.dumps(details, ensure_ascii=False, separators=(",", ":"), sort_keys=True) if details else None,
    ))


def deliver_password_reset(account: BillingAccount, raw_token: str, expires_at: datetime) -> None:
    """Deliver a reset challenge through the deployment's configured security channel."""
    settings = get_settings()
    if settings.security_delivery_mode == "development":
        return
    if settings.security_delivery_mode != "webhook" or not settings.security_delivery_webhook_url or not account.security_contact or not account.security_contact_verified_at:
        raise HTTPException(status_code=503, detail="password reset delivery is not configured")
    payload = json.dumps({
        "event": "password_reset",
        "contact": account.security_contact,
        "login_id": account.login_id,
        "reset_token": raw_token,
        "expires_at": expires_at.isoformat(),
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(settings.security_delivery_webhook_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    try:
        response = httpx.post(
            settings.security_delivery_webhook_url,
            content=payload,
            headers={"Content-Type": "application/json", "X-LokToken-Signature": signature},
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="password reset delivery failed") from exc


def deliver_account_invitation(account: BillingAccount, raw_token: str, expires_at: datetime) -> str:
    """Deliver a first-password link without treating an unverified contact as trusted yet."""
    settings = get_settings()
    # Keep the one-time credential out of HTTP access logs and Referer headers.
    setup_url = f"{settings.public_base_url.rstrip('/')}/portal#invite_token={quote(raw_token)}"
    if settings.security_delivery_mode == "development":
        return setup_url
    if settings.security_delivery_mode != "webhook" or not settings.security_delivery_webhook_url or not account.security_contact:
        raise HTTPException(status_code=503, detail="account invitation delivery is not configured")
    payload = json.dumps({
        "event": "account_invitation",
        "contact": account.security_contact,
        "login_id": account.login_id,
        "setup_url": setup_url,
        "expires_at": expires_at.isoformat(),
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(settings.security_delivery_webhook_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    try:
        response = httpx.post(
            settings.security_delivery_webhook_url,
            content=payload,
            headers={"Content-Type": "application/json", "X-LokToken-Signature": signature},
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="account invitation delivery failed") from exc
    return setup_url


def create_security_contact_challenge(db: Session, account: BillingAccount, contact: str) -> tuple[SecurityContactChallenge, str]:
    settings = get_settings()
    raw_token = create_password_reset_token().replace("rst_", "vfy_", 1)
    challenge = SecurityContactChallenge(
        account_id=account.id,
        contact=contact,
        token_hash=hash_key(raw_token),
        expires_at=utcnow() + timedelta(seconds=settings.password_reset_ttl_seconds),
    )
    if settings.security_delivery_mode != "development":
        if settings.security_delivery_mode != "webhook" or not settings.security_delivery_webhook_url:
            raise HTTPException(status_code=503, detail="security contact verification delivery is not configured")
        payload = json.dumps({
            "event": "security_contact_verification",
            "contact": contact,
            "login_id": account.login_id,
            "verification_token": raw_token,
            "expires_at": challenge.expires_at.isoformat(),
        }, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(settings.security_delivery_webhook_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        try:
            response = httpx.post(settings.security_delivery_webhook_url, content=payload, headers={"Content-Type": "application/json", "X-LokToken-Signature": signature}, timeout=10)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="security contact verification delivery failed") from exc
    db.add(challenge)
    return challenge, raw_token


def portal_account(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> BillingAccount:
    return require_portal_context(authorization, db).account


def portal_context(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> PortalContext:
    return require_portal_context(authorization, db)


def order_data(order: PaymentOrder) -> dict[str, object]:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "amount_micros": order.amount_micros,
        "provider": order.provider,
        "provider_order_id": order.provider_order_id,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "refunded_at": order.refunded_at.isoformat() if order.refunded_at else None,
    }


def scoped_usage_query(
    account_id: int,
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    status: str | None = None,
    request_id: str | None = None,
):
    query = select(UsageRecord).where(UsageRecord.account_id == account_id)
    if model:
        query = query.where(UsageRecord.model == model)
    if api_key_id:
        query = query.where(UsageRecord.api_key_id == api_key_id)
    if from_at:
        query = query.where(UsageRecord.created_at >= from_at)
    if to_at:
        query = query.where(UsageRecord.created_at <= to_at)
    if status:
        query = query.where(UsageRecord.status == status)
    if request_id:
        query = query.where(UsageRecord.request_id.contains(request_id))
    return query


def usage_record_data(record: UsageRecord, key_name: str) -> dict[str, object]:
    return {
        "id": record.id,
        "request_id": record.request_id,
        "trace_id": record.trace_id,
        "api_key_id": record.api_key_id,
        "api_key_name": key_name,
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


def csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@router.post("/admin/trial-links", dependencies=[Depends(require_operator)])
def create_trial_link(payload: TrialLinkCreate, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    account = db.get(BillingAccount, payload.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=404, detail="active account not found")
    token, expires_at = create_trial_token(account, payload.expires_in_seconds)
    configured_base = get_settings().public_base_url.rstrip("/")
    base_url = configured_base or str(request.base_url).rstrip("/")
    return {
        "account_id": account.id,
        "access_token": token,
        "portal_url": f"{base_url}/portal#access_token={quote(token, safe='')}",
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
    }


def auth_response(account: BillingAccount) -> dict[str, object]:
    token, expires_at = create_portal_session_token(account)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
        "account": {"id": account.id, "name": account.name, "external_user_id": account.external_user_id},
    }


def oidc_enabled() -> bool:
    settings = get_settings()
    return bool(
        settings.oidc_enabled
        and settings.oidc_issuer_url
        and settings.oidc_client_id
        and settings.oidc_client_secret
        and settings.oidc_authorization_endpoint
        and settings.oidc_token_endpoint
        and settings.oidc_userinfo_endpoint
        and settings.oidc_redirect_uri
    )


def loksystem_sso_enabled() -> bool:
    settings = get_settings()
    parsed_url = urlparse(settings.loksystem_sso_base_url)
    return bool(
        settings.loksystem_sso_enabled
        and parsed_url.scheme == "http"
        and parsed_url.hostname in {"127.0.0.1", "localhost", "::1"}
    )


def _loksystem_sso_frontend_url() -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/portal"


async def _request_loksystem_sso_user() -> dict[str, object]:
    if not loksystem_sso_enabled():
        raise HTTPException(status_code=404, detail="LokSystem local sign-in is not configured")
    base_url = get_settings().loksystem_sso_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            ticket_response = await client.post(f"{base_url}/api/loktoken/sso/tickets")
            ticket_response.raise_for_status()
            ticket_payload = ticket_response.json()
            ticket = ticket_payload.get("ticket") if isinstance(ticket_payload, dict) else None
            if not isinstance(ticket, str) or not ticket:
                raise ValueError("LokSystem SSO ticket response was invalid")
            user_response = await client.post(
                f"{base_url}/api/loktoken/sso/tickets/consume",
                json={"ticket": ticket},
            )
            user_response.raise_for_status()
            user_payload = user_response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise HTTPException(status_code=401, detail="Please sign in to LokSystem desktop first") from exc
        raise HTTPException(status_code=502, detail="LokSystem desktop sign-in failed") from exc
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="LokSystem desktop is unavailable") from exc
    if not isinstance(user_payload, dict) or not isinstance(user_payload.get("user"), dict):
        raise HTTPException(status_code=502, detail="LokSystem SSO user response was invalid")
    return user_payload["user"]


def _loksystem_account(db: Session, claims: dict[str, object]) -> BillingAccount:
    subject = str(claims.get("id") or "").strip()
    if not subject:
        raise HTTPException(status_code=502, detail="LokSystem SSO user response was invalid")
    settings = get_settings()
    issuer = settings.loksystem_sso_issuer.rstrip("/")
    identity = db.scalar(select(ExternalIdentity).where(ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject))
    if identity:
        account = db.get(BillingAccount, identity.account_id)
        if not account or not account.active:
            raise HTTPException(status_code=403, detail="billing account is inactive")
        return account
    external_user_id = f"loksystem-{subject}"
    if len(external_user_id) > 120:
        external_user_id = "loksystem-" + hashlib.sha256(subject.encode("utf-8")).hexdigest()[:40]
    account = db.scalar(select(BillingAccount).where(BillingAccount.external_user_id == external_user_id))
    if not account:
        account = BillingAccount(
            external_user_id=external_user_id,
            login_id=None,
            password_hash=None,
            name=str(claims.get("username") or claims.get("email") or subject)[:120],
            account_source="loksystem",
            access_mode="portal",
        )
        db.add(account)
        db.flush()
    elif not account.active:
        raise HTTPException(status_code=403, detail="billing account is inactive")
    db.add(ExternalIdentity(
        account_id=account.id,
        provider="loksystem",
        issuer=issuer,
        subject=subject,
        email=str(claims.get("email") or "").strip().lower() or None,
    ))
    ensure_personal_workspace(db, account)
    return account


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _oidc_frontend_url() -> str:
    settings = get_settings()
    return (settings.oidc_frontend_redirect_url or f"{settings.public_base_url.rstrip('/')}/portal").rstrip("/")


@router.get("/auth/oidc/status")
def oidc_status() -> dict[str, object]:
    return {"enabled": oidc_enabled(), "provider": "LokSystem" if oidc_enabled() else None}


@router.get("/auth/loksystem/status")
def loksystem_sso_status() -> dict[str, object]:
    return {"enabled": loksystem_sso_enabled(), "provider": "LokSystem" if loksystem_sso_enabled() else None}


@router.get("/auth/loksystem/start")
async def loksystem_sso_start(db: Session = Depends(get_db)) -> RedirectResponse:
    try:
        claims = await _request_loksystem_sso_user()
        account = _loksystem_account(db, claims)
        subject = str(claims["id"])
        record_audit_event(db, actor_type="loksystem", actor_id=subject, action="account.loksystem_sso_login", target_type="account", target_id=account.id, details={"issuer": get_settings().loksystem_sso_issuer.rstrip("/")})
        record_security_notification(db, account, "account_loksystem_sso_login")
        db.commit()
    except HTTPException as exc:
        return RedirectResponse(f"{_loksystem_sso_frontend_url()}#sso_error={quote(str(exc.detail), safe='')}", status_code=302)
    token = auth_response(account)["access_token"]
    return RedirectResponse(f"{_loksystem_sso_frontend_url()}#access_token={quote(str(token), safe='')}", status_code=302)


@router.get("/auth/oidc/start")
def oidc_start(db: Session = Depends(get_db)) -> RedirectResponse:
    if not oidc_enabled():
        raise HTTPException(status_code=404, detail="unified identity login is not configured")
    settings = get_settings()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    db.add(OidcLoginChallenge(
        state_hash=hash_key(state),
        nonce=nonce,
        code_verifier=verifier,
        expires_at=utcnow() + timedelta(seconds=settings.password_reset_ttl_seconds),
    ))
    db.commit()
    query = urlencode({
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    })
    return RedirectResponse(f"{settings.oidc_authorization_endpoint}?{query}", status_code=302)


def _oidc_account(db: Session, issuer: str, subject: str, claims: dict[str, object]) -> BillingAccount:
    identity = db.scalar(select(ExternalIdentity).where(ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject))
    if identity:
        account = db.get(BillingAccount, identity.account_id)
        if not account or not account.active:
            raise HTTPException(status_code=403, detail="billing account is inactive")
        return account
    settings = get_settings()
    stable_id = str(claims.get(settings.oidc_account_id_claim) or "").strip()
    if not stable_id:
        stable_id = "oidc-" + hashlib.sha256(f"{issuer}:{subject}".encode("utf-8")).hexdigest()[:40]
    account = db.scalar(select(BillingAccount).where(BillingAccount.external_user_id == stable_id))
    if not account:
        if not settings.oidc_allow_account_creation:
            raise HTTPException(status_code=403, detail="unified identity is not linked to a LokToken account")
        email = str(claims.get("email") or "").strip().lower() or None
        account = BillingAccount(
            external_user_id=stable_id[:120],
            name=str(claims.get("name") or claims.get("preferred_username") or email or subject)[:120],
            account_source="oidc",
            access_mode="portal",
            login_id=None,
            password_hash=None,
            security_contact=email if email and claims.get("email_verified") is True else None,
            security_contact_verified_at=utcnow() if email and claims.get("email_verified") is True else None,
        )
        db.add(account)
        db.flush()
    elif not account.active:
        raise HTTPException(status_code=403, detail="billing account is inactive")
    db.add(ExternalIdentity(
        account_id=account.id,
        provider="oidc",
        issuer=issuer,
        subject=subject,
        email=str(claims.get("email") or "").strip().lower() or None,
    ))
    ensure_personal_workspace(db, account)
    return account


@router.get("/auth/oidc/callback")
async def oidc_callback(code: str, state: str, db: Session = Depends(get_db)) -> RedirectResponse:
    if not oidc_enabled():
        raise HTTPException(status_code=404, detail="unified identity login is not configured")
    challenge = db.scalar(select(OidcLoginChallenge).where(
        OidcLoginChallenge.state_hash == hash_key(state),
        OidcLoginChallenge.consumed_at.is_(None),
    ))
    challenge_expiry = challenge.expires_at if challenge and challenge.expires_at.tzinfo else (challenge.expires_at.replace(tzinfo=timezone.utc) if challenge else None)
    if not challenge or challenge_expiry <= utcnow():
        raise HTTPException(status_code=400, detail="unified identity login state is invalid or expired")
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post(settings.oidc_token_endpoint, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
                "code_verifier": challenge.code_verifier,
            })
            token_response.raise_for_status()
            token_payload = token_response.json()
            access_token = token_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise ValueError("OIDC token response did not contain access_token")
            userinfo_response = await client.get(settings.oidc_userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"})
            userinfo_response.raise_for_status()
            claims = userinfo_response.json()
        if not isinstance(claims, dict) or not claims.get("sub"):
            raise ValueError("OIDC userinfo response did not contain sub")
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="unified identity provider request failed") from exc
    account = _oidc_account(db, settings.oidc_issuer_url.rstrip("/"), str(claims["sub"]), claims)
    challenge.consumed_at = utcnow()
    record_audit_event(db, actor_type="oidc", actor_id=str(claims["sub"]), action="account.oidc_login", target_type="account", target_id=account.id, details={"issuer": settings.oidc_issuer_url.rstrip("/")})
    record_security_notification(db, account, "account_oidc_login")
    db.commit()
    token = auth_response(account)["access_token"]
    return RedirectResponse(f"{_oidc_frontend_url()}#access_token={quote(str(token), safe='')}", status_code=302)


@router.post("/auth/register")
def register(payload: PortalRegister, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("auth-register", request.client.host if request.client else "unknown", settings.auth_rate_limit_requests, settings.auth_rate_limit_window_seconds)
    login_id = payload.login_id.lower()
    if db.scalar(select(BillingAccount).where(BillingAccount.login_id == login_id)):
        raise HTTPException(status_code=409, detail="login id already exists")
    account = BillingAccount(
        external_user_id=f"user-{uuid.uuid4().hex[:20]}",
        login_id=login_id,
        password_hash=hash_password(payload.password),
        name=payload.name,
        account_source="self_registered",
        access_mode="portal",
        security_contact=payload.security_contact.strip() if payload.security_contact else None,
    )
    try:
        db.add(account)
        db.flush()
        ensure_personal_workspace(db, account)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="login id already exists") from exc
    verification_token = None
    if account.security_contact:
        _, verification_token = create_security_contact_challenge(db, account, account.security_contact)
    record_audit_event(db, actor_type="public", actor_id=login_id, action="account.registered", target_type="account", target_id=account.id, details={"security_contact_pending": bool(account.security_contact)})
    record_security_notification(db, account, "account_registered")
    db.commit()
    db.refresh(account)
    response = auth_response(account)
    response["security_contact_verification_required"] = bool(account.security_contact)
    if verification_token and settings.security_delivery_mode == "development":
        response["development_verification_token"] = verification_token
    return response


@router.post("/auth/login")
def login(payload: PortalLogin, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("auth-login", request.client.host if request.client else "unknown", settings.auth_rate_limit_requests, settings.auth_rate_limit_window_seconds)
    account = db.scalar(select(BillingAccount).where(BillingAccount.login_id == payload.login_id.lower()))
    if not account or not account.password_hash or not verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="invalid login credentials")
    if not account.active:
        raise HTTPException(status_code=403, detail="billing account is inactive")
    record_security_notification(db, account, "account_logged_in")
    db.commit()
    return auth_response(account)


@router.post("/auth/password-reset/request")
def request_password_reset(payload: PasswordResetRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("password-reset-request", request.client.host if request.client else "unknown", settings.auth_rate_limit_requests, settings.auth_rate_limit_window_seconds)
    account = db.scalar(select(BillingAccount).where(BillingAccount.login_id == payload.login_id.lower()))
    response: dict[str, object] = {"accepted": True}
    if not account or not account.active or not account.password_hash or not account.security_contact or not account.security_contact_verified_at:
        return response
    raw_token = create_password_reset_token()
    challenge = PasswordResetChallenge(
        account_id=account.id,
        token_hash=hash_key(raw_token),
        purpose="password_reset",
        expires_at=utcnow() + timedelta(seconds=settings.password_reset_ttl_seconds),
    )
    deliver_password_reset(account, raw_token, challenge.expires_at)
    db.add(challenge)
    record_audit_event(db, actor_type="public", actor_id=account.external_user_id, action="account.password_reset_requested", target_type="account", target_id=account.id, details={})
    record_security_notification(db, account, "password_reset_requested")
    db.commit()
    # Development exposes the token only for local UAT. Production delivery must use an external notifier.
    if settings.security_delivery_mode == "development":
        response["development_reset_token"] = raw_token
    return response


@router.post("/auth/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)) -> dict[str, object]:
    challenge = db.scalar(select(PasswordResetChallenge).where(
        PasswordResetChallenge.token_hash == hash_key(payload.reset_token),
        PasswordResetChallenge.consumed_at.is_(None),
    ))
    if not challenge:
        raise HTTPException(status_code=400, detail="password reset token is invalid or expired")
    now = utcnow()
    expires_at = challenge.expires_at if challenge.expires_at.tzinfo else challenge.expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise HTTPException(status_code=400, detail="password reset token is invalid or expired")
    account = db.get(BillingAccount, challenge.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=403, detail="billing account is inactive")
    account.password_hash = hash_password(payload.password)
    account.session_version += 1
    challenge.consumed_at = now
    invitation = challenge.purpose == "invitation"
    if invitation:
        account.security_contact_verified_at = now
    action = "account.invitation_accepted" if invitation else "account.password_reset_completed"
    event_type = "account_invitation_accepted" if invitation else "password_reset_completed"
    record_audit_event(db, actor_type="public", actor_id=account.external_user_id, action=action, target_type="account", target_id=account.id, details={})
    record_security_notification(db, account, event_type)
    db.commit()
    return auth_response(account)


@router.get("/portal/profile")
def profile(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    personal_workspace = ensure_personal_workspace(db, account)
    db.commit()
    return {
        "id": account.id,
        "external_user_id": account.external_user_id,
        "name": account.name,
        "balance_micros": account.balance_micros,
        "api_key_count": db.scalar(select(func.count(ApiKey.id)).where(ApiKey.account_id == account.id, ApiKey.active.is_(True))) or 0,
        "request_count": db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.account_id == account.id)) or 0,
        "security_contact": account.security_contact,
        "security_contact_verified_at": account.security_contact_verified_at.isoformat() if account.security_contact_verified_at else None,
        "personal_workspace_id": personal_workspace.id,
        "created_at": account.created_at.isoformat(),
    }


@router.get("/portal/workspaces")
def list_workspaces(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = accessible_workspaces(db, account)
    db.commit()
    return {"data": [workspace_data(db, workspace, role) for workspace, role in rows]}


@router.post("/portal/organizations")
def create_portal_organization(payload: OrganizationCreate, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    organization, workspace, project = create_organization(db, account, payload.name)
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="organization.created", target_type="organization", target_id=organization.id, details={"workspace_id": workspace.id})
    db.commit()
    return {"id": organization.id, "name": organization.name, "slug": organization.slug, "workspace_id": workspace.id, "default_project_id": project.id}


@router.get("/portal/workspaces/{workspace_id}/projects")
def list_workspace_projects(workspace_id: int, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    workspace, role = workspace_access(db, account, workspace_id)
    from .models import Project
    projects = db.scalars(select(Project).where(Project.workspace_id == workspace.id).order_by(Project.id)).all()
    return {"workspace": workspace_data(db, workspace, role), "data": [{"id": project.id, "name": project.name, "slug": project.slug, "active": project.active, "created_at": project.created_at.isoformat()} for project in projects]}


@router.post("/portal/workspaces/{workspace_id}/projects")
def create_workspace_project(workspace_id: int, payload: ProjectCreate, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    workspace, role = workspace_access(db, account, workspace_id)
    require_workspace_manager(role)
    project = create_project(db, workspace, payload.name, payload.slug)
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="project.created", target_type="project", target_id=project.id, details={"workspace_id": workspace.id})
    db.commit()
    return {"id": project.id, "workspace_id": workspace.id, "name": project.name, "slug": project.slug, "active": project.active}


@router.get("/portal/workspaces/{workspace_id}/members")
def list_workspace_members(workspace_id: int, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    workspace, role = workspace_access(db, account, workspace_id)
    if not workspace.organization_id:
        return {"data": [{"account_id": account.id, "name": account.name, "login_id": account.login_id, "role": "owner"}]}
    rows = db.execute(select(OrganizationMember, BillingAccount).join(BillingAccount, BillingAccount.id == OrganizationMember.account_id).where(OrganizationMember.organization_id == workspace.organization_id).order_by(OrganizationMember.id)).all()
    return {"data": [{"account_id": member.account_id, "name": member_account.name, "login_id": member_account.login_id, "role": member.role, "created_at": member.created_at.isoformat()} for member, member_account in rows], "role": role}


@router.post("/portal/workspaces/{workspace_id}/members")
def add_workspace_member(workspace_id: int, payload: OrganizationMemberCreate, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    workspace, role = workspace_access(db, account, workspace_id)
    require_workspace_manager(role)
    if not workspace.organization_id:
        raise HTTPException(status_code=422, detail="personal workspaces do not support members")
    member_account = db.scalar(select(BillingAccount).where(BillingAccount.login_id == payload.login_id.lower(), BillingAccount.active.is_(True)))
    if not member_account:
        raise HTTPException(status_code=404, detail="active user account not found")
    existing = db.scalar(select(OrganizationMember).where(OrganizationMember.organization_id == workspace.organization_id, OrganizationMember.account_id == member_account.id))
    if existing:
        raise HTTPException(status_code=409, detail="user is already a workspace member")
    member = OrganizationMember(organization_id=workspace.organization_id, account_id=member_account.id, role=payload.role)
    db.add(member)
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="organization.member_added", target_type="organization", target_id=workspace.organization_id, details={"account_id": member_account.id, "role": payload.role})
    db.commit()
    return {"account_id": member_account.id, "name": member_account.name, "login_id": member_account.login_id, "role": member.role}


@router.get("/portal/api-keys")
def list_api_keys(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    keys = db.scalars(select(ApiKey).where(ApiKey.account_id == account.id).order_by(ApiKey.id.desc())).all()
    return {"data": [{
        "id": item.id,
        "project_id": item.project_id,
        "name": item.name,
        "key_prefix": item.key_prefix,
        "active": item.active,
        "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
        "revoke_reason": item.revoke_reason,
        "rate_limit_requests": item.rate_limit_requests,
        "rate_limit_window_seconds": item.rate_limit_window_seconds,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "spending_limit_micros": item.spending_limit_micros,
        "spent_micros": item.spent_micros,
        "trial_expires_at": item.trial_expires_at.isoformat() if item.trial_expires_at else None,
        "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None,
        "created_at": item.created_at.isoformat(),
    } for item in keys]}


@router.post("/portal/api-keys/{api_key_id}/rotate")
def rotate_api_key(api_key_id: int, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    api_key = db.scalar(select(ApiKey).where(ApiKey.id == api_key_id, ApiKey.account_id == account.id))
    if not api_key:
        raise HTTPException(status_code=404, detail="api key not found")
    if not api_key.active or api_key.revoked_at:
        raise HTTPException(status_code=409, detail="only an active api key can be rotated")
    if api_key.project_id:
        _, _, role = project_access(db, account, api_key.project_id)
        require_workspace_manager(role)
    raw_key = create_key()
    replacement = ApiKey(
        account_id=account.id,
        project_id=api_key.project_id,
        name=f"{api_key.name} (rotated)",
        key_prefix=raw_key[:12],
        key_hash=hash_key(raw_key),
        expires_at=api_key.expires_at,
        trial_expires_at=api_key.trial_expires_at,
        spending_limit_micros=api_key.spending_limit_micros,
        spent_micros=api_key.spent_micros,
        rate_limit_requests=api_key.rate_limit_requests,
        rate_limit_window_seconds=api_key.rate_limit_window_seconds,
        rotated_from_key_id=api_key.id,
    )
    api_key.active = False
    db.add(replacement)
    db.flush()
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="api_key.rotated", target_type="api_key", target_id=replacement.id, details={"replaced_api_key_id": api_key.id})
    record_security_notification(db, account, "api_key_rotated", {"replaced_api_key_id": api_key.id, "replacement_api_key_id": replacement.id})
    db.commit()
    return {"id": replacement.id, "name": replacement.name, "key": raw_key, "key_prefix": replacement.key_prefix, "replaced_api_key_id": api_key.id}


@router.get("/portal/api-keys/{api_key_id}/transactions")
def api_key_transactions(api_key_id: int, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    key = db.scalar(select(ApiKey).where(ApiKey.id == api_key_id, ApiKey.account_id == account.id))
    if not key:
        raise HTTPException(status_code=404, detail="api key not found")
    rows = db.scalars(select(AccountBalanceTransaction).where(AccountBalanceTransaction.account_id == account.id, AccountBalanceTransaction.api_key_id == api_key_id).order_by(AccountBalanceTransaction.id.desc()).limit(100)).all()
    return {"data": [{"id": row.id, "amount_micros": row.amount_micros, "type": row.transaction_type, "reference_id": row.reference_id, "description": row.description, "created_at": row.created_at.isoformat()} for row in rows]}


@router.put("/portal/security/contact")
def bind_security_contact(payload: SecurityContactUpdate, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    if not account.password_hash or not verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="invalid account password")
    account.security_contact = payload.contact.strip()
    account.security_contact_verified_at = None
    _, raw_token = create_security_contact_challenge(db, account, account.security_contact)
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="account.security_contact_verification_requested", target_type="account", target_id=account.id, details={})
    record_security_notification(db, account, "security_contact_verification_requested")
    db.commit()
    response: dict[str, object] = {"security_contact": account.security_contact, "security_contact_verified_at": None, "verification_required": True}
    if get_settings().security_delivery_mode == "development":
        response["development_verification_token"] = raw_token
    return response


@router.post("/portal/security/contact/confirm")
def confirm_security_contact(payload: SecurityContactConfirm, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    challenge = db.scalar(select(SecurityContactChallenge).where(
        SecurityContactChallenge.account_id == account.id,
        SecurityContactChallenge.token_hash == hash_key(payload.verification_token),
        SecurityContactChallenge.consumed_at.is_(None),
    ))
    if not challenge:
        raise HTTPException(status_code=400, detail="security contact verification token is invalid or expired")
    now = utcnow()
    expires_at = challenge.expires_at if challenge.expires_at.tzinfo else challenge.expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now or account.security_contact != challenge.contact:
        raise HTTPException(status_code=400, detail="security contact verification token is invalid or expired")
    challenge.consumed_at = now
    account.security_contact_verified_at = now
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="account.security_contact_verified", target_type="account", target_id=account.id, details={})
    record_security_notification(db, account, "security_contact_verified")
    db.commit()
    return {"security_contact": account.security_contact, "security_contact_verified_at": account.security_contact_verified_at.isoformat()}


@router.get("/portal/security-notifications")
def security_notifications(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    notifications = db.scalars(
        select(SecurityNotification)
        .where(SecurityNotification.account_id == account.id)
        .order_by(SecurityNotification.id.desc())
        .limit(100)
    ).all()
    return {"data": [{
        "id": item.id,
        "event_type": item.event_type,
        "details": json.loads(item.details_json) if item.details_json else {},
        "read_at": item.read_at.isoformat() if item.read_at else None,
        "created_at": item.created_at.isoformat(),
    } for item in notifications]}


@router.post("/portal/security/logout-all")
def logout_portal_sessions(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, bool]:
    account.session_version += 1
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="account.sessions_revoked", target_type="account", target_id=account.id, details={})
    record_security_notification(db, account, "portal_sessions_revoked")
    db.commit()
    return {"revoked": True}


@router.post("/portal/api-keys")
def create_api_key(payload: PortalApiKeyCreate, account: BillingAccount = Depends(portal_account), context: PortalContext = Depends(portal_context), db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("portal-key-create", str(account.id), settings.portal_rate_limit_requests, settings.portal_rate_limit_window_seconds)
    if payload.idempotency_key:
        existing = db.scalar(select(ApiKey).where(ApiKey.idempotency_key == payload.idempotency_key, ApiKey.account_id == account.id))
        if existing:
            raise HTTPException(status_code=409, detail="idempotency key already used")
    exact_expiry = payload.expires_at
    if exact_expiry and exact_expiry.tzinfo is None:
        exact_expiry = exact_expiry.replace(tzinfo=timezone.utc)
    if exact_expiry and exact_expiry <= utcnow():
        raise HTTPException(status_code=422, detail="expires_at must be in the future")
    raw_key = create_key()
    trial_expires_at = context.expires_at if context.token_type == "trial" else None
    if trial_expires_at and (exact_expiry is None or exact_expiry > trial_expires_at):
        exact_expiry = trial_expires_at
    if payload.project_id:
        project, _, role = project_access(db, account, payload.project_id)
        require_workspace_manager(role)
    else:
        project = ensure_default_project(db, ensure_personal_workspace(db, account))
    record = ApiKey(
        account_id=account.id,
        project_id=project.id,
        name=payload.name,
        key_prefix=raw_key[:12],
        key_hash=hash_key(raw_key),
        expires_at=exact_expiry or (utcnow() + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None),
        trial_expires_at=trial_expires_at,
        spending_limit_micros=payload.spending_limit_micros,
        idempotency_key=payload.idempotency_key,
        rate_limit_requests=payload.rate_limit_requests,
        rate_limit_window_seconds=payload.rate_limit_window_seconds,
    )
    db.add(record)
    db.flush()
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="api_key.created", target_type="api_key", target_id=record.id, details={"name": record.name})
    record_security_notification(db, account, "api_key_created", {"api_key_id": record.id, "name": record.name})
    db.commit()
    db.refresh(record)
    return {"id": record.id, "project_id": record.project_id, "name": record.name, "key": raw_key, "key_prefix": record.key_prefix}


@router.patch("/portal/api-keys/{api_key_id}")
def update_api_key(api_key_id: int, payload: ActiveUpdate, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    api_key = db.scalar(select(ApiKey).where(ApiKey.id == api_key_id, ApiKey.account_id == account.id))
    if not api_key:
        raise HTTPException(status_code=404, detail="api key not found")
    if api_key.project_id:
        _, _, role = project_access(db, account, api_key.project_id)
        require_workspace_manager(role)
    if api_key.revoked_at and payload.active:
        raise HTTPException(status_code=409, detail="revoked api key cannot be re-enabled")
    if payload.revoke:
        api_key.active = False
        api_key.revoked_at = utcnow()
        api_key.revoke_reason = payload.revoke_reason or "用户撤销"
        event = "api_key.revoked"
    else:
        api_key.active = payload.active
        event = "api_key.status_updated"
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action=event, target_type="api_key", target_id=api_key.id, details={"active": api_key.active, "revoked": bool(api_key.revoked_at), "reason": api_key.revoke_reason})
    record_security_notification(db, account, "api_key_revoked" if payload.revoke else "api_key_status_updated", {"api_key_id": api_key.id, "active": api_key.active})
    db.commit()
    return {"id": api_key.id, "active": api_key.active}


@router.post("/portal/model-tests")
async def test_model(
    payload: PortalModelTestRequest,
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Run one bounded, billable chat request from the model marketplace."""
    settings = get_settings()
    rate_limiter.check("portal-model-test", str(account.id), settings.portal_rate_limit_requests, settings.portal_rate_limit_window_seconds)
    api_key = db.scalar(select(ApiKey).where(ApiKey.id == payload.api_key_id, ApiKey.account_id == account.id))
    if not api_key or not api_key.active or api_key.revoked_at:
        raise HTTPException(status_code=422, detail="请选择有效的 API Key")
    model = db.scalar(select(ModelConfig).where(ModelConfig.public_name == payload.model, ModelConfig.active.is_(True)))
    if not model:
        raise HTTPException(status_code=404, detail="model not found")
    try:
        metadata = json.loads(model.catalog_metadata_json) if model.catalog_metadata_json else {}
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    api_type = metadata.get("api_type", "chat_completions")
    if api_type in {"images_generations", "video_generations"}:
        if not model_is_callable(db, model):
            raise HTTPException(status_code=503, detail="model unavailable")
        request_id = "test_" + uuid.uuid4().hex
        trace_id = request_id
        quantity = payload.n if api_type == "images_generations" else 1
        reservation = model.task_price_micros * quantity
        try:
            reserve_balance(db, account, api_key, reservation, request_id)
        except ValueError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        task = GenerationTask(task_id="task_" + uuid.uuid4().hex, account_id=account.id, api_key_id=api_key.id, model_config_id=model.id, request_id=request_id, trace_id=trace_id, task_type=api_type, status="processing", quantity=quantity, reserved_micros=reservation)
        db.add(task)
        db.commit()
        try:
            task_payload = {"model": model.public_name, "prompt": payload.prompt.strip(), "n": payload.n}
            if api_type == "audio_speech":
                task_payload = {"model": model.public_name, "input": payload.prompt.strip(), "voice": "alloy"}
            elif api_type == "audio_transcriptions":
                if not payload.audio:
                    raise HTTPException(status_code=422, detail="语音识别测试需要提供音频内容")
                task_payload = {"model": model.public_name, "audio": payload.audio, "filename": "test-audio.wav"}
            if payload.size:
                task_payload["size"] = payload.size
            if payload.duration_seconds:
                task_payload["duration_seconds"] = payload.duration_seconds
            detail = await create_provider_task(db, model, task_payload)
            task.provider_channel_id = detail.channel_id
            task.provider_task_id = detail.provider_task_id
            task.status = detail.status
            task.result_json = json.dumps(detail.result, ensure_ascii=False)
            task.updated_at = utcnow()
            if detail.status in {"completed", "failed"}:
                task.error_message = None if detail.status == "completed" else "provider task failed"
                settle_balance(db, account, api_key, reservation, reservation if detail.status == "completed" else 0, request_id)
                save_usage(db, api_key, model, request_id, trace_id, 0, 0, "success" if detail.status == "completed" else "error", 0, task.error_message, provider_cost_micros=detail.provider_cost_micros, provider_channel_id=detail.channel_id, provider_request_id=detail.provider_task_id, raw_usage={"task_id": task.task_id, "task_type": api_type, "quantity": quantity, "result": detail.result}, amount_micros=reservation if detail.status == "completed" else 0)
                task.settled_at = utcnow()
            db.commit()
            return {"request_id": request_id, "model": model.public_name, "status": task.status, "response": detail.result, "amount_micros": reservation if detail.status == "completed" else 0}
        except Exception as exc:
            task.status = "failed"
            task.error_message = str(exc)
            db.commit()
            settle_balance(db, account, api_key, reservation, 0, request_id)
            raise HTTPException(status_code=502, detail="模型测试调用失败，请稍后重试") from exc
    request = ChatCompletionRequest(
        model=model.public_name,
        messages=[{"role": "user", "content": payload.prompt.strip()}],
        max_tokens=payload.max_tokens,
        stream=False,
    )
    if api_type != "chat_completions":
        raise HTTPException(status_code=422, detail="当前模型的统一调用适配器尚未启用")
    if not model_is_callable(db, model):
        raise HTTPException(status_code=503, detail="model unavailable")
    validate_model_request(model, request)
    request_id = "test_" + uuid.uuid4().hex
    trace_id = request_id
    estimated_input = estimate_tokens(request.messages)
    reservation = calculate_amount(model, estimated_input, payload.max_tokens)
    try:
        reserve_balance(db, account, api_key, reservation, request_id)
    except ValueError as exc:
        save_usage(db, api_key, model, request_id, trace_id, estimated_input, 0, "rejected", 0, str(exc), amount_micros=0)
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    started = time.perf_counter()
    try:
        provider_result = await call_provider_details(db, model, request)
        actual_amount = calculate_amount(model, provider_result.input_tokens, provider_result.output_tokens)
        settle_balance(db, account, api_key, reservation, actual_amount, request_id)
        save_usage(
            db, api_key, model, request_id, trace_id,
            provider_result.input_tokens, provider_result.output_tokens, "success",
            int((time.perf_counter() - started) * 1000),
            provider_cost_micros=provider_result.provider_cost_micros,
            provider_channel_id=provider_result.channel_id,
            provider_request_id=provider_result.provider_request_id,
            usage_details=provider_result.usage_details,
            raw_usage=provider_result.raw_usage,
            route_attempts=provider_result.route_attempts,
            amount_micros=actual_amount,
        )
        return {
            "request_id": request_id,
            "model": model.public_name,
            "response": provider_result.response,
            "input_tokens": provider_result.input_tokens,
            "output_tokens": provider_result.output_tokens,
            "amount_micros": actual_amount,
        }
    except HTTPException as exc:
        settle_balance(db, account, api_key, reservation, 0, request_id)
        save_usage(db, api_key, model, request_id, trace_id, estimated_input, 0, "error", int((time.perf_counter() - started) * 1000), str(exc.detail), amount_micros=0)
        raise
    except Exception as exc:
        settle_balance(db, account, api_key, reservation, 0, request_id)
        save_usage(db, api_key, model, request_id, trace_id, estimated_input, 0, "error", int((time.perf_counter() - started) * 1000), str(exc), amount_micros=0)
        raise HTTPException(status_code=502, detail="模型测试调用失败，请稍后重试") from exc


@router.get("/portal/models")
def list_models(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    del account
    settings = get_settings()
    models = [model for model in db.scalars(select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.public_name)).all() if model_is_callable(db, model)]
    data = []
    for item in models:
        channels = db.scalars(select(ModelChannel).where(ModelChannel.model_config_id == item.id)).all()
        active_channels = [channel for channel in channels if channel.active]
        healthy_channels = [channel for channel in active_channels if channel.status == "healthy"]
        if not active_channels:
            health_status = "unavailable"
        elif healthy_channels:
            health_status = "healthy"
        elif any(channel.status == "unknown" for channel in active_channels):
            health_status = "checking"
        else:
            health_status = "degraded"
        data.append({
            "id": item.id,
            "public_name": item.public_name,
            "input_price_micros_per_1k": item.input_price_micros_per_1k,
            "output_price_micros_per_1k": item.output_price_micros_per_1k,
            "task_price_micros": item.task_price_micros,
            "rate_limit": {
                "requests": settings.api_rate_limit_requests,
                "window_seconds": settings.api_rate_limit_window_seconds,
            },
            "health_status": health_status,
            "channel_count": len(channels),
            "active_channel_count": len(active_channels),
            "healthy_channel_count": len(healthy_channels),
            "health_details": [{"name": channel.name, "status": channel.status, "health_source": channel.health_source, "last_checked_at": channel.last_checked_at.isoformat() if channel.last_checked_at else None, "last_latency_ms": channel.last_latency_ms, "last_status_code": channel.last_status_code, "last_error": channel.last_error} for channel in active_channels],
            "last_checked_at": max((channel.last_checked_at for channel in active_channels if channel.last_checked_at), default=None).isoformat() if any(channel.last_checked_at for channel in active_channels) else None,
            **model_metadata(item.public_name, item.catalog_metadata_json),
        })
    return {"data": data}


@router.get("/portal/usage")
def usage_summary(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    status: str | None = None,
    request_id: str | None = None,
) -> dict[str, object]:
    record_ids = scoped_usage_query(
        account.id, model, api_key_id, from_at, to_at, status, request_id
    ).with_only_columns(UsageRecord.id)
    count, inputs, outputs, total, amount, average_latency, success_count = db.execute(select(
        func.count(UsageRecord.id),
        func.coalesce(func.sum(UsageRecord.input_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        func.coalesce(func.sum(UsageRecord.total_tokens), 0),
        func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        func.coalesce(func.avg(UsageRecord.latency_ms), 0),
        func.count(UsageRecord.id).filter(UsageRecord.status == "success"),
    ).where(UsageRecord.id.in_(record_ids))).one()
    failed_count = count - success_count
    return {
        "request_count": count,
        "input_tokens": inputs,
        "output_tokens": outputs,
        "total_tokens": total,
        "amount_micros": amount,
        "provider_cost_micros": db.scalar(select(func.coalesce(func.sum(UsageRecord.provider_cost_micros), 0)).where(UsageRecord.id.in_(record_ids), UsageRecord.status == "success")) or 0,
        "average_latency_ms": round(float(average_latency), 1),
        "success_count": success_count,
        "failed_count": failed_count,
        "success_rate": round(success_count / count * 100, 1) if count else 0,
    }


@router.get("/portal/usage/analytics")
def usage_analytics(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    status: str | None = None,
    request_id: str | None = None,
    granularity: Annotated[str, Query(pattern="^(hour|day)$")] = "day",
) -> dict[str, object]:
    record_ids = scoped_usage_query(
        account.id, model, api_key_id, from_at, to_at, status, request_id
    ).with_only_columns(UsageRecord.id)
    if db.get_bind().dialect.name == "postgresql":
        bucket = (
            func.date_trunc("hour", UsageRecord.created_at)
            if granularity == "hour"
            else cast(UsageRecord.created_at, Date)
        ).label("bucket")
    else:
        bucket_format = "%Y-%m-%dT%H:00:00" if granularity == "hour" else "%Y-%m-%d"
        bucket = func.strftime(bucket_format, UsageRecord.created_at).label("bucket")
    trend_rows = db.execute(
        select(
            bucket,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
            func.coalesce(func.avg(UsageRecord.latency_ms), 0),
        )
        .where(UsageRecord.id.in_(record_ids))
        .group_by(bucket)
        .order_by(bucket)
    ).all()
    model_rows = db.execute(
        select(
            UsageRecord.model,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        )
        .where(UsageRecord.id.in_(record_ids))
        .group_by(UsageRecord.model)
        .order_by(func.sum(UsageRecord.total_tokens).desc(), func.count(UsageRecord.id).desc())
    ).all()
    key_rows = db.execute(
        select(
            ApiKey.id,
            ApiKey.name,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        )
        .join(UsageRecord, UsageRecord.api_key_id == ApiKey.id)
        .where(UsageRecord.id.in_(record_ids))
        .group_by(ApiKey.id, ApiKey.name)
        .order_by(func.sum(UsageRecord.total_tokens).desc(), func.count(UsageRecord.id).desc())
    ).all()
    return {
        "granularity": granularity,
        "trend": [
            {
                "bucket": item_bucket,
                "request_count": count,
                "input_tokens": inputs,
                "output_tokens": outputs,
                "total_tokens": total,
                "amount_micros": amount,
                "average_latency_ms": round(float(latency), 1),
            }
            for item_bucket, count, inputs, outputs, total, amount, latency in trend_rows
        ],
        "model_distribution": [
            {"name": item_model, "request_count": count, "total_tokens": total, "amount_micros": amount}
            for item_model, count, total, amount in model_rows
        ],
        "key_distribution": [
            {"id": key_id, "name": key_name, "request_count": count, "total_tokens": total, "amount_micros": amount}
            for key_id, key_name, count, total, amount in key_rows
        ],
    }


@router.get("/portal/dashboard")
def dashboard(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    days: Annotated[int, Query(ge=7, le=90)] = 7,
    model: str | None = None,
    api_key_id: int | None = None,
) -> dict[str, object]:
    if days not in {7, 30, 90}:
        raise HTTPException(status_code=422, detail="days must be one of 7, 30 or 90")
    today = utcnow().date()
    period_start = today - timedelta(days=days - 1)

    base_filters = [UsageRecord.account_id == account.id]
    if model:
        base_filters.append(UsageRecord.model == model)
    if api_key_id:
        base_filters.append(UsageRecord.api_key_id == api_key_id)
    period_filters = [*base_filters, UsageRecord.created_at >= datetime.combine(period_start, datetime.min.time(), timezone.utc)]

    day_column = (
        cast(UsageRecord.created_at, Date)
        if db.get_bind().dialect.name == "postgresql"
        else func.date(UsageRecord.created_at)
    ).label("day")
    daily_rows = db.execute(
        select(
            day_column,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        )
        .where(*period_filters)
        .group_by(day_column)
        .order_by(day_column)
    ).all()
    daily_map = {
        str(day): {"request_count": count, "total_tokens": tokens, "amount_micros": amount}
        for day, count, tokens, amount in daily_rows
    }
    daily = []
    for offset in range(days):
        day = period_start + timedelta(days=offset)
        values = daily_map.get(day.isoformat(), {"request_count": 0, "total_tokens": 0, "amount_micros": 0})
        daily.append({"date": day.isoformat(), **values})

    ranking_rows = db.execute(
        select(
            UsageRecord.model,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        )
        .where(*period_filters)
        .group_by(UsageRecord.model)
        .order_by(func.sum(UsageRecord.amount_micros).desc(), func.count(UsageRecord.id).desc())
        .limit(5)
    ).all()

    activity_start = today - timedelta(days=364)
    activity_filters = [
        *base_filters,
        UsageRecord.created_at >= datetime.combine(activity_start, datetime.min.time(), timezone.utc),
    ]
    activity_rows = db.execute(
        select(day_column, func.count(UsageRecord.id), func.coalesce(func.sum(UsageRecord.amount_micros), 0))
        .where(*activity_filters)
        .group_by(day_column)
        .order_by(day_column)
    ).all()
    activity = [{"date": str(day), "request_count": count, "amount_micros": amount} for day, count, amount in activity_rows]
    activity_dates = sorted(datetime.fromisoformat(item["date"]).date() for item in activity)
    longest_streak = 0
    current_streak = 0
    previous_day = None
    for activity_day in activity_dates:
        current_streak = current_streak + 1 if previous_day and activity_day == previous_day + timedelta(days=1) else 1
        longest_streak = max(longest_streak, current_streak)
        previous_day = activity_day

    total_amount = db.scalar(select(func.coalesce(func.sum(UsageRecord.amount_micros), 0)).where(*base_filters)) or 0
    today_amount = sum(item["amount_micros"] for item in activity if item["date"] == today.isoformat())
    week_start = today - timedelta(days=6)
    week_amount = sum(
        item["amount_micros"]
        for item in activity
        if datetime.fromisoformat(item["date"]).date() >= week_start
    )
    period_request_count = sum(item["request_count"] for item in daily)
    period_total_tokens = sum(item["total_tokens"] for item in daily)
    period_amount = sum(item["amount_micros"] for item in daily)

    return {
        "period": {
            "days": days,
            "from": period_start.isoformat(),
            "to": today.isoformat(),
            "request_count": period_request_count,
            "total_tokens": period_total_tokens,
            "amount_micros": period_amount,
        },
        "daily": daily,
        "model_ranking": [
            {"model": item_model, "request_count": count, "total_tokens": tokens, "amount_micros": amount}
            for item_model, count, tokens, amount in ranking_rows
        ],
        "activity": activity,
        "activity_summary": {
            "longest_streak_days": longest_streak,
            "today_amount_micros": today_amount,
            "week_amount_micros": week_amount,
            "total_amount_micros": total_amount,
        },
    }


@router.get("/portal/usage/records")
def usage_records(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    status: str | None = None,
    request_id: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=10, le=100)] = 20,
) -> dict[str, object]:
    record_ids = scoped_usage_query(
        account.id, model, api_key_id, from_at, to_at, status, request_id
    ).with_only_columns(UsageRecord.id)
    total = db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.id.in_(record_ids))) or 0
    query = select(UsageRecord, ApiKey.name).join(ApiKey, ApiKey.id == UsageRecord.api_key_id).where(UsageRecord.id.in_(record_ids))
    rows = db.execute(
        query.order_by(UsageRecord.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "data": [usage_record_data(record, key_name) for record, key_name in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/portal/usage/records/{request_id}")
def usage_record_detail(
    request_id: str,
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    row = db.execute(
        select(UsageRecord, ApiKey.name)
        .join(ApiKey, ApiKey.id == UsageRecord.api_key_id)
        .where(UsageRecord.account_id == account.id, UsageRecord.request_id == request_id)
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="request record not found")
    return usage_record_data(row[0], row[1])


@router.get("/portal/usage/export")
def export_usage(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    status: str | None = None,
    request_id: str | None = None,
) -> Response:
    record_ids = scoped_usage_query(
        account.id, model, api_key_id, from_at, to_at, status, request_id
    ).with_only_columns(UsageRecord.id)
    rows = db.execute(
        select(UsageRecord, ApiKey.name)
        .join(ApiKey, ApiKey.id == UsageRecord.api_key_id)
        .where(UsageRecord.id.in_(record_ids))
        .order_by(UsageRecord.id.desc())
        .limit(5000)
    ).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["created_at", "request_id", "trace_id", "api_key", "model", "input_tokens", "output_tokens", "total_tokens", "latency_ms", "amount_micros", "provider_cost_micros", "provider_channel_id", "provider_request_id", "status", "error_message"])
    for record, key_name in rows:
        writer.writerow([
            record.created_at.isoformat(), csv_safe(record.request_id), csv_safe(record.trace_id), csv_safe(key_name), csv_safe(record.model),
            record.input_tokens, record.output_tokens, record.total_tokens, record.latency_ms,
            record.amount_micros, record.provider_cost_micros, record.provider_channel_id, csv_safe(record.provider_request_id or ""), csv_safe(record.status), csv_safe(record.error_message or ""),
        ])
    filename = f"token-usage-{datetime.now(timezone.utc).date().isoformat()}.csv"
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/portal/transactions")
def transactions(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    items = db.scalars(select(AccountBalanceTransaction).where(
        AccountBalanceTransaction.account_id == account.id
    ).order_by(AccountBalanceTransaction.id.desc()).limit(100)).all()
    return {"data": [{
        "id": item.id,
        "amount_micros": item.amount_micros,
        "type": item.transaction_type,
        "reference_id": item.reference_id,
        "description": item.description,
        "created_at": item.created_at.isoformat(),
    } for item in items]}


@router.get("/portal/balance-summary")
def balance_summary(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    total_credit = db.scalar(select(func.coalesce(func.sum(AccountBalanceTransaction.amount_micros), 0)).where(
        AccountBalanceTransaction.account_id == account.id,
        AccountBalanceTransaction.transaction_type.in_(("topup", "payment", "redemption")),
    )) or 0
    total_consumed = db.scalar(select(func.coalesce(func.sum(UsageRecord.amount_micros), 0)).where(
        UsageRecord.account_id == account.id,
        UsageRecord.status == "success",
    )) or 0
    transaction_count = db.scalar(select(func.count(AccountBalanceTransaction.id)).where(
        AccountBalanceTransaction.account_id == account.id
    )) or 0
    return {
        "balance_micros": account.balance_micros,
        "total_credit_micros": total_credit,
        "total_consumed_micros": total_consumed,
        "transaction_count": transaction_count,
    }


@router.get("/portal/payment-providers")
def list_payment_providers(account: BillingAccount = Depends(portal_account)) -> dict[str, object]:
    del account
    return {"data": [provider.to_dict() for provider in payment_providers()]}


@router.get("/portal/payment-orders")
def list_payment_orders(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    orders = db.scalars(select(PaymentOrder).where(PaymentOrder.account_id == account.id).order_by(PaymentOrder.id.desc()).limit(100)).all()
    return {"data": [order_data(item) for item in orders]}


@router.post("/portal/payment-orders")
def create_payment_order(payload: PaymentOrderCreate, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("portal-payment-create", str(account.id), settings.portal_rate_limit_requests, settings.portal_rate_limit_window_seconds)
    if payload.account_id != account.id:
        raise HTTPException(status_code=403, detail="payment order account mismatch")
    try:
        require_available_provider(payload.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.project_id:
        project, workspace, _ = project_access(db, account, payload.project_id)
    else:
        workspace = ensure_personal_workspace(db, account)
        project = ensure_default_project(db, workspace)
    order = PaymentOrder(
        order_no="pay_" + uuid.uuid4().hex,
        account_id=account.id,
        workspace_id=workspace.id,
        project_id=project.id,
        amount_micros=payload.amount_micros,
        provider=payload.provider,
    )
    db.add(order)
    db.flush()
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="payment_order.created", target_type="payment_order", target_id=order.id, details={"order_no": order.order_no, "amount_micros": order.amount_micros, "provider": order.provider})
    db.commit()
    db.refresh(order)
    return order_data(order)


def redemption_code_is_expired(code: RedemptionCode) -> bool:
    if not code.expires_at:
        return False
    now = utcnow()
    if code.expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return code.expires_at <= now


@router.post("/portal/redemption-codes/redeem")
def redeem_code(
    payload: RedemptionCodeRedeem,
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("portal-redemption", str(account.id), settings.portal_rate_limit_requests, settings.portal_rate_limit_window_seconds)
    raw_code = payload.code.strip()
    code = db.scalar(select(RedemptionCode).where(RedemptionCode.code_hash == hash_key(raw_code)).with_for_update())
    if not code:
        raise HTTPException(status_code=404, detail="redemption code not found")
    existing_claim = db.scalar(select(RedemptionClaim).where(
        RedemptionClaim.redemption_code_id == code.id,
        RedemptionClaim.account_id == account.id,
    ))
    if existing_claim:
        raise HTTPException(status_code=409, detail="redemption code already used by this account")
    if not code.active or redemption_code_is_expired(code) or code.redeemed_count >= code.max_redemptions:
        raise HTTPException(status_code=422, detail="redemption code is unavailable")
    locked_account = db.scalar(select(BillingAccount).where(BillingAccount.id == account.id).with_for_update())
    if not locked_account or not locked_account.active:
        raise HTTPException(status_code=403, detail="billing account is inactive")
    reference_id = f"redemption:{code.id}:{account.id}"
    claim = RedemptionClaim(
        redemption_code_id=code.id,
        account_id=account.id,
        amount_micros=code.amount_micros,
        reference_id=reference_id,
    )
    locked_account.balance_micros += code.amount_micros
    code.redeemed_count += 1
    db.add(claim)
    db.add(AccountBalanceTransaction(
        account_id=account.id,
        api_key_id=None,
        amount_micros=code.amount_micros,
        transaction_type="redemption",
        reference_id=reference_id,
        description=f"redeemed benefit: {code.label}",
    ))
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="redemption_code.claimed", target_type="redemption_code", target_id=code.id, details={"amount_micros": code.amount_micros})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="redemption code could not be claimed") from exc
    db.refresh(claim)
    return {
        "id": claim.id,
        "label": code.label,
        "amount_micros": claim.amount_micros,
        "balance_micros": locked_account.balance_micros,
        "redeemed_at": claim.redeemed_at.isoformat(),
    }


@router.get("/portal/redemptions")
def list_redemptions(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = db.execute(
        select(RedemptionClaim, RedemptionCode.label)
        .join(RedemptionCode, RedemptionCode.id == RedemptionClaim.redemption_code_id)
        .where(RedemptionClaim.account_id == account.id)
        .order_by(RedemptionClaim.id.desc())
        .limit(100)
    ).all()
    return {"data": [{
        "id": claim.id,
        "label": label,
        "amount_micros": claim.amount_micros,
        "redeemed_at": claim.redeemed_at.isoformat(),
    } for claim, label in rows]}
