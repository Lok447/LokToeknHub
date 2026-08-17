import hashlib
import hmac
import base64
import binascii
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Header, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import ApiKey, BillingAccount, utcnow


def hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_key() -> str:
    return "tok_" + secrets.token_urlsafe(32)


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


def create_portal_session_token(account: BillingAccount, expires_in_seconds: int | None = None) -> tuple[str, int]:
    settings = get_settings()
    expires_at = int(time.time()) + (expires_in_seconds or settings.portal_session_ttl_seconds)
    payload = _base64url_encode(json.dumps({
        "aud": "token-portal",
        "typ": "session",
        "sub": account.external_user_id,
        "account_id": account.id,
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
        if token_type == "session" and claims.get("typ") != "session":
            valid_claims = False
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        valid_signature = False
        claims = {}
        valid_claims = False
    if not valid_signature or not valid_claims:
        raise HTTPException(status_code=401, detail="invalid or expired trial access token")
    account = db.get(BillingAccount, claims.get("account_id"))
    if not account or not account.active or account.external_user_id != claims.get("sub"):
        raise HTTPException(status_code=403, detail="billing account is inactive")
    return PortalContext(
        account=account,
        token_type=token_type,
        expires_at=datetime.fromtimestamp(int(claims["exp"]), timezone.utc),
    )


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


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_token
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")


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
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > utcnow()),
        or_(ApiKey.trial_expires_at.is_(None), ApiKey.trial_expires_at > utcnow()),
    ))
    if not record:
        raise HTTPException(status_code=401, detail="invalid api key")
    account = db.get(BillingAccount, record.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=401, detail="invalid api key")
    return record
