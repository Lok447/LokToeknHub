"""Initial TOKEN platform schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "billing_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_user_id", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("balance_micros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("external_user_id", name="uq_billing_accounts_external_user_id"),
    )
    op.create_index("ix_billing_accounts_external_user_id", "billing_accounts", ["external_user_id"])
    op.create_index("ix_billing_accounts_active", "billing_accounts", ["active"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("billing_accounts.id"), nullable=False),
        sa.Column("key_prefix", sa.String(length=24), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_account_id", "api_keys", ["account_id"])
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])
    op.create_index("ix_api_keys_active", "api_keys", ["active"])

    op.create_table(
        "model_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_name", sa.String(length=120), nullable=False),
        sa.Column("upstream_model", sa.String(length=120), nullable=False),
        sa.Column("provider_base_url", sa.String(length=500), nullable=False),
        sa.Column("provider_api_key_env", sa.String(length=120), nullable=True),
        sa.Column("input_price_micros_per_1k", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_price_micros_per_1k", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("public_name", name="uq_model_configs_public_name"),
    )
    op.create_index("ix_model_configs_public_name", "model_configs", ["public_name"])
    op.create_index("ix_model_configs_active", "model_configs", ["active"])

    op.create_table(
        "account_balance_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("billing_accounts.id"), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("amount_micros", sa.Integer(), nullable=False),
        sa.Column("transaction_type", sa.String(length=32), nullable=False),
        sa.Column("reference_id", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("reference_id", name="uq_account_balance_transactions_reference_id"),
    )
    op.create_index("ix_account_balance_transactions_account_id", "account_balance_transactions", ["account_id"])
    op.create_index("ix_account_balance_transactions_api_key_id", "account_balance_transactions", ["api_key_id"])
    op.create_index("ix_account_balance_transactions_transaction_type", "account_balance_transactions", ["transaction_type"])
    op.create_index("ix_account_balance_transactions_reference_id", "account_balance_transactions", ["reference_id"])
    op.create_index("ix_account_balance_transactions_created_at", "account_balance_transactions", ["created_at"])

    op.create_table(
        "payment_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_no", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("billing_accounts.id"), nullable=False),
        sa.Column("amount_micros", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("provider_order_id", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("order_no", name="uq_payment_orders_order_no"),
        sa.UniqueConstraint("provider_order_id", name="uq_payment_orders_provider_order_id"),
    )
    op.create_index("ix_payment_orders_order_no", "payment_orders", ["order_no"])
    op.create_index("ix_payment_orders_account_id", "payment_orders", ["account_id"])
    op.create_index("ix_payment_orders_provider", "payment_orders", ["provider"])
    op.create_index("ix_payment_orders_status", "payment_orders", ["status"])
    op.create_index("ix_payment_orders_created_at", "payment_orders", ["created_at"])

    op.create_table(
        "usage_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("billing_accounts.id"), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("upstream_model", sa.String(length=120), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_micros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("request_id", name="uq_usage_records_request_id"),
    )
    op.create_index("ix_usage_records_request_id", "usage_records", ["request_id"])
    op.create_index("ix_usage_records_trace_id", "usage_records", ["trace_id"])
    op.create_index("ix_usage_records_account_id", "usage_records", ["account_id"])
    op.create_index("ix_usage_records_api_key_id", "usage_records", ["api_key_id"])
    op.create_index("ix_usage_records_model", "usage_records", ["model"])
    op.create_index("ix_usage_records_status", "usage_records", ["status"])
    op.create_index("ix_usage_records_created_at", "usage_records", ["created_at"])


def downgrade() -> None:
    op.drop_table("usage_records")
    op.drop_table("payment_orders")
    op.drop_table("account_balance_transactions")
    op.drop_table("model_configs")
    op.drop_table("api_keys")
    op.drop_table("billing_accounts")
