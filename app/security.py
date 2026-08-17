import hashlib
import hmac
import base64
import binascii
import json
import secrets
import time

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
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


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


def require_trial_account(authorization: str | None, db: Session) -> BillingAccount:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing trial access token")
    token = authorization[7:].strip()
    if not token.startswith("trl_") or "." not in token:
        raise HTTPException(status_code=401, detail="invalid trial access token")
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
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        valid_signature = False
        claims = {}
        valid_claims = False
    if not valid_signature or not valid_claims:
        raise HTTPException(status_code=401, detail="invalid or expired trial access token")
    account = db.get(BillingAccount, claims.get("account_id"))
    if not account or not account.active or account.external_user_id != claims.get("sub"):
        raise HTTPException(status_code=403, detail="billing account is inactive")
    return account


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
    ))
    if not record:
        raise HTTPException(status_code=401, detail="invalid api key")
    return record
