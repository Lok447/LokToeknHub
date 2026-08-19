"""Encryption for provider credentials entered through the admin console."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class ProviderSecretError(ValueError):
    pass


def _fernet() -> Fernet:
    settings = get_settings()
    raw_key = settings.provider_secrets_key.strip()
    if not raw_key and settings.environment.lower() != "production":
        raw_key = f"loktoken-development-only:{settings.admin_token}:{settings.database_url}"
    if len(raw_key) < 32:
        raise ProviderSecretError("TOKEN_PROVIDER_SECRETS_KEY must be configured with at least 32 characters")
    derived = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
    return Fernet(derived)


def encrypt_provider_secret(value: str) -> str:
    secret = value.strip()
    if not secret:
        raise ProviderSecretError("provider secret cannot be empty")
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_provider_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise ProviderSecretError("provider secret cannot be decrypted with current TOKEN_PROVIDER_SECRETS_KEY") from exc
