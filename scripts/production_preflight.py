"""Validate production release prerequisites without contacting external providers."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from app.config import Settings
from app.payment_providers import payment_providers


def value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    checks = {
        "TOKEN_ENVIRONMENT": value("TOKEN_ENVIRONMENT") == "production",
        "TOKEN_AUTO_CREATE_SCHEMA": value("TOKEN_AUTO_CREATE_SCHEMA", "false").lower() == "false",
        "TOKEN_MOCK_MODE": value("TOKEN_MOCK_MODE", "false").lower() == "false",
        "TOKEN_DATABASE_URL": urlparse(value("TOKEN_DATABASE_URL")).scheme.startswith("postgresql"),
        "TOKEN_REDIS_URL": urlparse(value("TOKEN_REDIS_URL")).scheme in {"redis", "rediss"},
        "TOKEN_PUBLIC_BASE_URL": urlparse(value("TOKEN_PUBLIC_BASE_URL")).scheme == "https",
        "TOKEN_REQUIRE_REAL_PAYMENT": value("TOKEN_REQUIRE_REAL_PAYMENT", "true").lower() == "true",
    }
    for name, passed in checks.items():
        if not passed:
            errors.append(f"{name} is not production-ready")
    for name in ("TOKEN_ADMIN_TOKEN", "TOKEN_PROVIDER_SECRETS_KEY", "TOKEN_PAYMENT_WEBHOOK_SECRET", "TOKEN_TRIAL_SIGNING_SECRET"):
        if len(value(name)) < 24 or value(name).startswith(("change-", "replace-")):
            errors.append(f"{name} must be a unique high-entropy secret")
    if value("TOKEN_REQUIRE_REAL_PAYMENT", "true").lower() == "true":
        settings = Settings()
        available_real_payment = any(
            provider.id != "manual" and provider.available
            for provider in payment_providers(settings)
        )
        if not available_real_payment:
            warnings.append(
                "no implemented and configured real payment provider is available; launch is blocked"
            )
    if not value("TOKEN_SECURITY_DELIVERY_WEBHOOK_URL").startswith("https://"):
        errors.append("TOKEN_SECURITY_DELIVERY_WEBHOOK_URL must use HTTPS")
    for line in [*([f"ERROR: {item}" for item in errors]), *([f"WARN: {item}" for item in warnings])]:
        print(line)
    status = "FAILED" if errors else "BLOCKED" if warnings else "PASSED"
    print(f"production preflight: {status}")
    return 1 if errors or warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
