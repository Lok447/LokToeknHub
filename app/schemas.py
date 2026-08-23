from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_id: int | None = Field(default=None, gt=0)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    spending_limit_micros: int | None = Field(default=None, gt=0)


class PortalApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    project_id: int | None = Field(default=None, gt=0)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    expires_at: datetime | None = None
    spending_limit_micros: int | None = Field(default=None, gt=0)


class TrialLinkCreate(BaseModel):
    account_id: int = Field(gt=0)
    expires_in_seconds: int | None = Field(default=None, ge=300, le=2592000)


class PortalRegister(BaseModel):
    login_id: str = Field(min_length=3, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,159}$")
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    security_contact: str | None = Field(default=None, min_length=3, max_length=160)


class PortalLogin(BaseModel):
    login_id: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=8, max_length=128)


class PasswordResetRequest(BaseModel):
    login_id: str = Field(min_length=3, max_length=160)


class PasswordResetConfirm(BaseModel):
    reset_token: str = Field(min_length=16, max_length=160)
    password: str = Field(min_length=8, max_length=128)


class SecurityContactUpdate(BaseModel):
    contact: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=8, max_length=128)


class SecurityContactConfirm(BaseModel):
    verification_token: str = Field(min_length=16, max_length=160)


class AdminLogin(BaseModel):
    login_id: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=8, max_length=128)


class AdminUserCreate(AdminLogin):
    role: Literal["superadmin", "operator", "auditor"] = "operator"


class AdminUserUpdate(BaseModel):
    role: Literal["superadmin", "operator", "auditor"] | None = None
    active: bool | None = None


class AccountCreate(BaseModel):
    external_user_id: str | None = Field(default=None, min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)

    @field_validator("external_user_id", "name", mode="before")
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class OrganizationMemberCreate(BaseModel):
    login_id: str = Field(min_length=3, max_length=160)
    role: Literal["admin", "member", "viewer"] = "member"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=2, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,119}$")


class ActiveUpdate(BaseModel):
    active: bool


class BalanceAdjust(BaseModel):
    amount_micros: int = Field(gt=0)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class PaymentOrderCreate(BaseModel):
    account_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)
    amount_micros: int = Field(gt=0)
    provider: str = Field(default="manual", min_length=1, max_length=32)


class RedemptionCodeCreate(BaseModel):
    label: str = Field(default="兑换福利", min_length=1, max_length=120)
    amount_micros: int = Field(gt=0)
    code: str | None = Field(default=None, min_length=8, max_length=120)
    max_redemptions: int = Field(default=1, ge=1, le=100000)
    expires_at: datetime | None = None


class RedemptionCodeRedeem(BaseModel):
    code: str = Field(min_length=8, max_length=120)


class PaymentConfirm(BaseModel):
    provider_order_id: str | None = Field(default=None, min_length=1, max_length=120)
    review_note: str | None = Field(default=None, max_length=500)


class PaymentRefund(BaseModel):
    review_note: str | None = Field(default=None, max_length=500)


class PaymentWebhook(BaseModel):
    event_id: str = Field(min_length=1, max_length=120)
    order_no: str = Field(min_length=1, max_length=64)
    provider_order_id: str = Field(min_length=1, max_length=120)
    status: Literal["paid"]


class ApiKeyResponse(BaseModel):
    id: int
    account_id: int
    name: str
    key: str
    key_prefix: str


class ModelCreate(BaseModel):
    public_name: str = Field(min_length=1, max_length=120)
    upstream_model: str = Field(min_length=1, max_length=120)
    provider_base_url: str | None = None
    provider_api_key_env: str | None = Field(default=None, max_length=120, pattern=r"^[A-Z][A-Z0-9_]{1,119}$")
    provider_api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    input_price_micros_per_1k: int = Field(default=0, ge=0)
    output_price_micros_per_1k: int = Field(default=0, ge=0)


class ModelBatchImport(BaseModel):
    provider_base_url: str = Field(min_length=1, max_length=500)
    provider_api_key_env: str | None = Field(default=None, max_length=120, pattern=r"^[A-Z][A-Z0-9_]{1,119}$")
    models: list[ModelCreate] = Field(min_length=1, max_length=100)


class ProviderPresetInstall(BaseModel):
    model_ids: list[str] = Field(min_length=1, max_length=20)


class ProviderConnectionConfigure(BaseModel):
    provider_base_url: str | None = Field(default=None, min_length=1, max_length=500)
    provider_api_key_env: str | None = Field(default=None, max_length=120, pattern=r"^[A-Z][A-Z0-9_]{1,119}$")
    provider_api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_provider_api_key: bool = False
    default_input_price_micros_per_1k: int = Field(default=0, ge=0)
    default_output_price_micros_per_1k: int = Field(default=0, ge=0)
    model_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    # Kept for backwards compatibility with older clients. New UI syncs never publish automatically.
    auto_publish: bool = False
    balance_alert_threshold_micros: int = Field(default=0, ge=0)


class ProviderBalanceManual(BaseModel):
    amount: float = Field(ge=0)
    currency: str = Field(default="CNY", min_length=3, max_length=12)
    note: str | None = Field(default=None, max_length=255)


class ModelUpdate(BaseModel):
    input_price_micros_per_1k: int | None = Field(default=None, ge=0)
    output_price_micros_per_1k: int | None = Field(default=None, ge=0)
    pricing_margin_bps: int | None = Field(default=None, ge=0, le=9900)
    active: bool | None = None


class ModelChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider_base_url: str = Field(min_length=1, max_length=500)
    upstream_model: str = Field(min_length=1, max_length=120)
    provider_api_key_env: str | None = Field(default=None, max_length=120, pattern=r"^[A-Z][A-Z0-9_]{1,119}$")
    provider_api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    priority: int = Field(default=100, ge=0, le=10000)
    weight: int = Field(default=100, ge=1, le=10000)
    provider_input_cost_micros_per_1k: int | None = Field(default=None, ge=0)
    provider_output_cost_micros_per_1k: int | None = Field(default=None, ge=0)
    active: bool = True


class ModelChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    provider_base_url: str | None = Field(default=None, min_length=1, max_length=500)
    upstream_model: str | None = Field(default=None, min_length=1, max_length=120)
    provider_api_key_env: str | None = Field(default=None, max_length=120, pattern=r"^[A-Z][A-Z0-9_]{1,119}$")
    provider_api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_provider_api_key: bool = False
    priority: int | None = Field(default=None, ge=0, le=10000)
    weight: int | None = Field(default=None, ge=1, le=10000)
    provider_input_cost_micros_per_1k: int | None = Field(default=None, ge=0)
    provider_output_cost_micros_per_1k: int | None = Field(default=None, ge=0)
    active: bool | None = None


class ModelPreflightRequest(BaseModel):
    chat_probe: bool = False
    stream_probe: bool = False
    prompt: str = Field(default="Respond with OK.", min_length=1, max_length=500)


class ProviderBillLineInput(BaseModel):
    line_key: str | None = Field(default=None, min_length=1, max_length=160)
    provider_request_id: str | None = Field(default=None, max_length=160)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    billed_cost_micros: int = Field(ge=0)
    raw: dict[str, Any] | None = None


class ProviderBillImportRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    source_name: str = Field(min_length=1, max_length=255)
    lines: list[ProviderBillLineInput] = Field(min_length=1, max_length=10000)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: Any


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1, max_length=120)
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0, le=262144)
    max_completion_tokens: int | None = Field(default=None, gt=0, le=262144)
    top_p: float | None = Field(default=None, ge=0, le=1)
    stop: str | list[str] | None = None
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    seed: int | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    reasoning_effort: str | None = Field(default=None, max_length=32)
    stream_options: dict[str, Any] | None = None
    stream: bool = False
    user: str | None = None


class UsageSummary(BaseModel):
    request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    amount_micros: int


class AccountBalance(BaseModel):
    account_id: int
    external_user_id: str
    api_key_id: int
    balance_micros: int
