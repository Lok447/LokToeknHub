import hashlib
import hmac
import base64
import binascii
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AdminSession, AdminUser, ApiKey, BillingAccount, utcnow


def hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_key() -> str:
    return "tok_" + secrets.token_urlsafe(32)


def create_admin_session_token() -> str:
    return "adm_" + secrets.token_urlsafe(32)


def create_password_reset_token() -> str:
    return "rst_" + secrets.token_urlsafe(32)


def create_redemption_code() -> str:
    return "rdm_" + secrets.token_urlsafe(20)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    # Reject alternate encodings that differ only in discarded Base64 padding bits.
    if _base64url_encode(decoded) != value:
        raise binascii.Error("non-canonical base64url encoding")
    return decoded


def create_trial_token(account: BillingAccount, expires_in_seconds: int | None = None) -> tuple[str, int]:
    settings = get_settings()
    expires_at = int(time.time()) + (expires_in_seconds or settings.trial_token_ttl_seconds)
    payload = _base64url_encode(json.dumps({
        "aud": "token-portal",
        "sub": account.external_user_id,
        "account_id": account.id,
        "jti": secrets.token_urlsafe(16),
        "exp": expires_at,
    }, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(settings.trial_signing_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return f"trl_{payload}.{_base64url_encode(signature)}", expires_at


def hash_password(password: str) -> str:
    """Hash account passwords without storing plaintext or requiring a native crypto package."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256$310000${_base64url_encode(salt)}${_base64url_encode(digest)}"


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        algorithm, rounds_text, salt_text, digest_text = (encoded or "").split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        salt = _base64url_decode(salt_text)
        expected = _base64url_decode(digest_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        return False


@dataclass(frozen=True)
class PortalContext:
    account: BillingAccount
    token_type: str
    expires_at: datetime | None
    trial_token_hash: str | None = None


@dataclass(frozen=True)
class AdminContext:
    user: AdminUser | None
    role: str
    actor_id: str
    session: AdminSession | None
    bootstrap: bool = False


def create_portal_session_token(account: BillingAccount, expires_in_seconds: int | None = None) -> tuple[str, int]:
    settings = get_settings()
    expires_at = int(time.time()) + (expires_in_seconds or settings.portal_session_ttl_seconds)
    payload = _base64url_encode(json.dumps({
        "aud": "token-portal",
        "typ": "session",
        "sub": account.external_user_id,
        "account_id": account.id,
        "sv": account.session_version,
        "exp": expires_at,
    }, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(settings.trial_signing_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return f"usr_{payload}.{_base64url_encode(signature)}", expires_at


def require_portal_context(authorization: str | None, db: Session) -> PortalContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing trial access token")
    token = authorization[7:].strip()
    if not (token.startswith("trl_") or token.startswith("usr_")) or "." not in token:
        raise HTTPException(status_code=401, detail="invalid trial access token")
    token_type = "trial" if token.startswith("trl_") else "session"
    payload, supplied_signature = token[4:].rsplit(".", 1)
    expected_signature = hmac.new(
        get_settings().trial_signing_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        valid_signature = secrets.compare_digest(_base64url_decode(supplied_signature), expected_signature)
        claims = json.loads(_base64url_decode(payload))
        if not isinstance(claims, dict):
            raise ValueError("trial token claims must be an object")
        valid_claims = claims.get("aud") == "token-portal" and int(claims.get("exp", 0)) > int(time.time())
        if token_type == "session":
            valid_claims = valid_claims and claims.get("typ") == "session"
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        valid_signature = False
        claims = {}
        valid_claims = False
    if not valid_signature or not valid_claims:
        raise HTTPException(status_code=401, detail="invalid or expired trial access token")
    account = db.get(BillingAccount, claims.get("account_id"))
    if not account or not account.active or account.external_user_id != claims.get("sub"):
        raise HTTPException(status_code=403, detail="billing account is inactive")
    if token_type == "session" and int(claims.get("sv", 0)) != account.session_version:
        raise HTTPException(status_code=401, detail="portal session has been revoked")
    return PortalContext(
        account=account,
        token_type=token_type,
        expires_at=datetime.fromtimestamp(int(claims["exp"]), timezone.utc),
        trial_token_hash=hash_key(token) if token_type == "trial" else None,
    )


def require_portal_session_context(authorization: str | None, db: Session) -> PortalContext:
    context = require_portal_context(authorization, db)
    if context.token_type != "session":
        raise HTTPException(status_code=401, detail="formal portal session required")
    return context


def require_trial_account(authorization: str | None, db: Session) -> BillingAccount:
    context = require_portal_context(authorization, db)
    if context.token_type != "trial":
        raise HTTPException(status_code=401, detail="invalid trial access token")
    return context.account


def verify_webhook_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    supplied = signature.removeprefix("sha256=")
    expected = hmac.new(
        get_settings().payment_webhook_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return secrets.compare_digest(supplied, expected)


def create_admin_session(db: Session, user: AdminUser) -> tuple[str, AdminSession]:
    raw_token = create_admin_session_token()
    session = AdminSession(
        admin_user_id=user.id,
        token_hash=hash_key(raw_token),
        expires_at=utcnow() + timedelta(seconds=get_settings().admin_session_ttl_seconds),
    )
    db.add(session)
    return raw_token, session


def _is_expired(value: datetime) -> bool:
    now = utcnow()
    if value.tzinfo is None:
        now = now.replace(tzinfo=None)
    return value <= now


def require_bootstrap_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_token
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bootstrap admin token")


def require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AdminContext:
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization[7:].strip()
        if raw_token.startswith("adm_"):
            session = db.scalar(select(AdminSession).where(
                AdminSession.token_hash == hash_key(raw_token),
                AdminSession.revoked_at.is_(None),
            ))
            if session and not _is_expired(session.expires_at):
                user = db.get(AdminUser, session.admin_user_id)
                if user and user.active:
                    return AdminContext(user=user, role=user.role, actor_id=user.login_id, session=session)

    expected = get_settings().admin_token
    admin_exists = db.scalar(select(AdminUser.id).limit(1)) is not None
    if not admin_exists and x_admin_token and secrets.compare_digest(x_admin_token, expected):
        return AdminContext(user=None, role="superadmin", actor_id="bootstrap-admin", session=None, bootstrap=True)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid administrator session")


def require_admin_roles(*roles: str) -> Callable[..., AdminContext]:
    def dependency(context: AdminContext = Depends(require_admin)) -> AdminContext:
        if context.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="administrator role is not permitted for this action")
        return context
    return dependency


require_operator = require_admin_roles("superadmin", "operator")
require_finance_operator = require_admin_roles("superadmin", "operator")
require_superadmin = require_admin_roles("superadmin")


def require_api_key(
    authorization: str | None,
    db: Session,
) -> ApiKey:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    raw_key = authorization[7:].strip()
    record = db.scalar(select(ApiKey).where(
        ApiKey.key_hash == hash_key(raw_key),
        ApiKey.active.is_(True),
        ApiKey.revoked_at.is_(None),
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > utcnow()),
        or_(ApiKey.trial_expires_at.is_(None), ApiKey.trial_expires_at > utcnow()),
    ))
    if not record:
        raise HTTPException(status_code=401, detail="invalid api key")
    account = db.get(BillingAccount, record.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=401, detail="invalid api key")
    return record
