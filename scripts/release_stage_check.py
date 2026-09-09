"""Check the minimum configuration for LokToken release stages A, B, and C."""

from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("A", "B", "C"), help="release stage to validate")
    args = parser.parse_args()
    stage = args.stage.upper()
    errors: list[str] = []
    warnings: list[str] = []

    if stage == "A":
        if env("TOKEN_ENVIRONMENT", "development") != "development":
            warnings.append("TOKEN_ENVIRONMENT is not development")
        if env("TOKEN_DATABASE_URL", "sqlite:///./token.db").startswith("postgresql"):
            print("stage A: PostgreSQL is supported, but local SQLite keeps setup fastest")
        print("stage A checks: local development, mock/provider sandbox, manual payment allowed")
    elif stage == "B":
        if env("TOKEN_ENVIRONMENT", "development") not in {"development", "staging"}:
            errors.append("TOKEN_ENVIRONMENT must be development or staging for stage B")
        if env("TOKEN_MOCK_MODE", "false").lower() != "false":
            errors.append("TOKEN_MOCK_MODE must be false for stage B")
        if not env("TOKEN_MANUAL_PAYMENT_QR_URL"):
            warnings.append("TOKEN_MANUAL_PAYMENT_QR_URL is empty; testers cannot see the manual payment QR")
        if env("TOKEN_PUBLIC_BASE_URL", "http://127.0.0.1:8000").startswith("http://"):
            warnings.append("stage B should use HTTPS when reachable beyond localhost")
        print("stage B checks: invited testers, real provider sandbox, manual payment review")
    else:
        if env("TOKEN_ENVIRONMENT") not in {"staging", "production"}:
            errors.append("TOKEN_ENVIRONMENT must be staging or production for stage C")
        if env("TOKEN_MOCK_MODE", "false").lower() != "false":
            errors.append("TOKEN_MOCK_MODE must be false for stage C")
        if env("TOKEN_PUBLIC_BASE_URL").startswith("http://") or urlparse(env("TOKEN_PUBLIC_BASE_URL")).scheme != "https":
            errors.append("TOKEN_PUBLIC_BASE_URL must be HTTPS for stage C")
        if env("TOKEN_REQUIRE_REAL_PAYMENT", "true").lower() == "true" and not env("TOKEN_PAYMENT_WEBHOOK_SECRET"):
            errors.append("TOKEN_PAYMENT_WEBHOOK_SECRET is required when real payment is enabled")
        if env("TOKEN_MANUAL_PAYMENT_QR_URL"):
            warnings.append("manual QR remains an offline review channel; do not present it as automatic payment")
        print("stage C checks: public free/invite test, HTTPS, rate limits, audit and rollback")

    for item in errors:
        print(f"ERROR: {item}")
    for item in warnings:
        print(f"WARN: {item}")
    status = "FAILED" if errors else "READY" if not warnings else "READY_WITH_WARNINGS"
    print(f"release stage {stage}: {status}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
