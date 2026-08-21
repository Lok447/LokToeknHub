from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BillingAccount(Base):
    __tablename__ = "billing_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_user_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    login_id: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    security_contact: Mapped[str | None] = mapped_column(String(160), nullable=True)
    security_contact_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    account_source: Mapped[str] = mapped_column(String(24), default="admin", index=True)
    name: Mapped[str] = mapped_column(String(120))
    balance_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    owner_account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "account_id", name="uq_organization_members_org_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("owner_account_id", "workspace_type", name="uq_workspaces_owner_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    workspace_type: Mapped[str] = mapped_column(String(24), default="personal", index=True)
    owner_account_id: Mapped[int | None] = mapped_column(ForeignKey("billing_accounts.id", ondelete="CASCADE"), nullable=True, index=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(24), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    rotated_from_key_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    spending_limit_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spent_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    upstream_model: Mapped[str] = mapped_column(String(120))
    provider_base_url: Mapped[str] = mapped_column(String(500))
    provider_api_key_env: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_price_micros_per_1k: Mapped[int] = mapped_column(BigInteger, default=0)
    output_price_micros_per_1k: Mapped[int] = mapped_column(BigInteger, default=0)
    # Stored in basis points: 100 bps = 1%. A null/zero value means manual pricing.
    pricing_margin_bps: Mapped[int] = mapped_column(Integer, default=0)
    catalog_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_pricing_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderConnection(Base):
    __tablename__ = "provider_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    preset_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    provider_base_url: Mapped[str] = mapped_column(String(500))
    provider_api_key_env: Mapped[str | None] = mapped_column(String(120), nullable=True)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_input_price_micros_per_1k: Mapped[int] = mapped_column(BigInteger, default=0)
    default_output_price_micros_per_1k: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    discovered_model_count: Mapped[int] = mapped_column(Integer, default=0)
    synced_model_count: Mapped[int] = mapped_column(Integer, default=0)
    callable_model_count: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    balance_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    balance_currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    balance_status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    balance_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    balance_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    balance_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    balance_alert_threshold_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderBalanceSnapshot(Base):
    __tablename__ = "provider_balance_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_connection_id: Mapped[int] = mapped_column(ForeignKey("provider_connections.id", ondelete="CASCADE"), index=True)
    amount_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    source: Mapped[str] = mapped_column(String(32))
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ModelChannel(Base):
    __tablename__ = "model_channels"
    __table_args__ = (UniqueConstraint("model_config_id", "name", name="uq_model_channels_model_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_config_id: Mapped[int] = mapped_column(ForeignKey("model_configs.id", ondelete="CASCADE"), index=True)
    provider_connection_id: Mapped[int | None] = mapped_column(ForeignKey("provider_connections.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    provider_base_url: Mapped[str] = mapped_column(String(500))
    upstream_model: Mapped[str] = mapped_column(String(120))
    provider_api_key_env: Mapped[str | None] = mapped_column(String(120), nullable=True)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    weight: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    health_source: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_input_cost_micros_per_1k: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_output_cost_micros_per_1k: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    api_key_id: Mapped[int] = mapped_column(Integer, index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    upstream_model: Mapped[str] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    amount_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    provider_cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    provider_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    input_cache_hit_tokens: Mapped[int] = mapped_column(Integer, default=0)
    input_cache_miss_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    price_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route_attempts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AccountBalanceTransaction(Base):
    __tablename__ = "account_balance_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    api_key_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    amount_micros: Mapped[int] = mapped_column(BigInteger)
    transaction_type: Mapped[str] = mapped_column(String(32), index=True)
    reference_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PaymentOrder(Base):
    __tablename__ = "payment_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    amount_micros: Mapped[int] = mapped_column(BigInteger)
    provider: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), default="operator", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PasswordResetChallenge(Base):
    __tablename__ = "password_reset_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SecurityContactChallenge(Base):
    __tablename__ = "security_contact_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    contact: Mapped[str] = mapped_column(String(160))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SecurityNotification(Base):
    __tablename__ = "security_notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AlertIncident(Base):
    __tablename__ = "alert_incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(24), index=True)
    title: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_notified_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    pending_event: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderBillImport(Base):
    __tablename__ = "provider_bill_imports"
    __table_args__ = (UniqueConstraint("provider", "source_hash", name="uq_provider_bill_imports_provider_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    source_name: Mapped[str] = mapped_column(String(255))
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    mismatch_count: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_count: Mapped[int] = mapped_column(Integer, default=0)
    billed_cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    recorded_cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ProviderBillLine(Base):
    __tablename__ = "provider_bill_lines"
    __table_args__ = (UniqueConstraint("import_id", "line_key", name="uq_provider_bill_lines_import_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("provider_bill_imports.id", ondelete="CASCADE"), index=True)
    line_key: Mapped[str] = mapped_column(String(160))
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    billed_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    billed_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    billed_cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    recorded_cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    usage_record_id: Mapped[int | None] = mapped_column(ForeignKey("usage_records.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    diff_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_external_identities_issuer_subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64), default="oidc", index=True)
    issuer: Mapped[str] = mapped_column(String(500), index=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OidcLoginChallenge(Base):
    __tablename__ = "oidc_login_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    nonce: Mapped[str] = mapped_column(String(128))
    code_verifier: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RedemptionCode(Base):
    __tablename__ = "redemption_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    code_prefix: Mapped[str] = mapped_column(String(24), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount_micros: Mapped[int] = mapped_column(BigInteger)
    max_redemptions: Mapped[int] = mapped_column(Integer, default=1)
    redeemed_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RedemptionClaim(Base):
    __tablename__ = "redemption_claims"
    __table_args__ = (UniqueConstraint("redemption_code_id", "account_id", name="uq_redemption_claims_code_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    redemption_code_id: Mapped[int] = mapped_column(ForeignKey("redemption_codes.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"), index=True)
    amount_micros: Mapped[int] = mapped_column(BigInteger)
    reference_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    redeemed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(32), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    target_type: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(120), index=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
