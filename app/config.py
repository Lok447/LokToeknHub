from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "sqlite:///./token.db"
    auto_create_schema: bool = True
    admin_token: str = "change-me"
    mock_mode: bool = True
    seed_builtin_models: bool = True
    default_provider_base_url: str = "http://localhost:4000/v1"
    default_provider_api_key: str = ""
    reservation_output_tokens: int = 1024
    provider_timeout_seconds: int = 120
    channel_health_timeout_seconds: int = 10
    max_channel_attempts: int = 3
    channel_failure_threshold: int = 3
    channel_circuit_cooldown_seconds: int = 60
    payment_webhook_secret: str = "change-webhook-secret"
    trial_signing_secret: str = "change-trial-secret"
    trial_token_ttl_seconds: int = 604800
    public_base_url: str = "http://127.0.0.1:8000"
    wechat_merchant_id: str = ""
    wechat_app_id: str = ""
    wechat_certificate_serial: str = ""
    wechat_private_key_path: str = ""
    alipay_app_id: str = ""
    alipay_private_key_path: str = ""
    alipay_public_key_path: str = ""
    cors_origins: str = ""
    max_request_body_bytes: int = 1_048_576
    api_rate_limit_requests: int = 120
    api_rate_limit_window_seconds: int = 60
    portal_rate_limit_requests: int = 60
    portal_rate_limit_window_seconds: int = 60
    auth_rate_limit_requests: int = 10
    auth_rate_limit_window_seconds: int = 60
    portal_session_ttl_seconds: int = 604800
    admin_session_ttl_seconds: int = 28800
    password_reset_ttl_seconds: int = 900
    security_delivery_mode: str = "development"
    security_delivery_webhook_url: str = ""
    security_delivery_webhook_secret: str = ""

    model_config = SettingsConfigDict(
        env_prefix="TOKEN_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def cors_origin_list(settings: Settings) -> list[str]:
    return [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]


def validate_startup_settings(settings: Settings) -> None:
    """Fail early when a production deployment still has development safeguards enabled."""
    if settings.environment.lower() != "production":
        return
    errors: list[str] = []
    if settings.auto_create_schema:
        errors.append("TOKEN_AUTO_CREATE_SCHEMA must be false in production")
    if settings.mock_mode:
        errors.append("TOKEN_MOCK_MODE must be false in production")
    defaults = {
        "TOKEN_ADMIN_TOKEN": settings.admin_token == "change-me" or len(settings.admin_token) < 24,
        "TOKEN_PAYMENT_WEBHOOK_SECRET": settings.payment_webhook_secret == "change-webhook-secret" or len(settings.payment_webhook_secret) < 24,
        "TOKEN_TRIAL_SIGNING_SECRET": settings.trial_signing_secret == "change-trial-secret" or len(settings.trial_signing_secret) < 24,
    }
    errors.extend(f"{name} must be a non-default secret with at least 24 characters" for name, invalid in defaults.items() if invalid)
    parsed_url = urlparse(settings.public_base_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        errors.append("TOKEN_PUBLIC_BASE_URL must be a public HTTPS URL in production")
    if settings.max_request_body_bytes < 16_384:
        errors.append("TOKEN_MAX_REQUEST_BODY_BYTES must be at least 16384")
    if settings.api_rate_limit_requests < 1 or settings.api_rate_limit_window_seconds < 1:
        errors.append("API rate limit settings must be positive")
    if settings.portal_rate_limit_requests < 1 or settings.portal_rate_limit_window_seconds < 1:
        errors.append("Portal rate limit settings must be positive")
    if settings.auth_rate_limit_requests < 1 or settings.auth_rate_limit_window_seconds < 1:
        errors.append("Auth rate limit settings must be positive")
    if settings.admin_session_ttl_seconds < 300 or settings.password_reset_ttl_seconds < 300:
        errors.append("Session and password reset TTL settings must be at least 300 seconds")
    if settings.security_delivery_mode != "webhook":
        errors.append("TOKEN_SECURITY_DELIVERY_MODE must be webhook in production")
    delivery_url = urlparse(settings.security_delivery_webhook_url)
    if delivery_url.scheme != "https" or not delivery_url.netloc:
        errors.append("TOKEN_SECURITY_DELIVERY_WEBHOOK_URL must be a public HTTPS URL in production")
    if len(settings.security_delivery_webhook_secret) < 24:
        errors.append("TOKEN_SECURITY_DELIVERY_WEBHOOK_SECRET must be at least 24 characters in production")
    if errors:
        raise RuntimeError("Invalid production configuration: " + "; ".join(errors))
