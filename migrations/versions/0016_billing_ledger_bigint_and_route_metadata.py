"""Use 64-bit money fields and persist provider route/cost metadata.

Revision ID: 0016_billing_ledger_bigint_and_route_metadata
Revises: 0015_security_contact_verification
Create Date: 2026-08-20
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0016_billing_ledger_bigint_and_route_metadata"
down_revision: str = "0015_security_contact_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One yuan is one million micros. INTEGER would cap a balance at about
    # 2,147 yuan in PostgreSQL, which is not sufficient for team accounts.
    for table, column in (
        ("billing_accounts", "balance_micros"),
        ("api_keys", "spending_limit_micros"),
        ("api_keys", "spent_micros"),
        ("model_configs", "input_price_micros_per_1k"),
        ("model_configs", "output_price_micros_per_1k"),
        ("account_balance_transactions", "amount_micros"),
        ("payment_orders", "amount_micros"),
        ("redemption_codes", "amount_micros"),
        ("redemption_claims", "amount_micros"),
        ("usage_records", "amount_micros"),
    ):
        op.alter_column(table, column, existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=column == "spending_limit_micros")

    op.add_column("model_channels", sa.Column("provider_input_cost_micros_per_1k", sa.BigInteger(), nullable=True))
    op.add_column("model_channels", sa.Column("provider_output_cost_micros_per_1k", sa.BigInteger(), nullable=True))
    op.add_column("usage_records", sa.Column("provider_cost_micros", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("usage_records", sa.Column("provider_channel_id", sa.Integer(), nullable=True))
    op.add_column("usage_records", sa.Column("provider_request_id", sa.String(length=160), nullable=True))
    op.add_column("usage_records", sa.Column("input_cache_hit_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("usage_records", sa.Column("input_cache_miss_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("usage_records", sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("usage_records", sa.Column("price_version", sa.String(length=64), nullable=True))
    op.add_column("usage_records", sa.Column("route_attempts_json", sa.Text(), nullable=True))
    op.add_column("usage_records", sa.Column("raw_usage_json", sa.Text(), nullable=True))
    op.create_index("ix_usage_records_provider_channel_id", "usage_records", ["provider_channel_id"])
    op.create_index("ix_usage_records_provider_request_id", "usage_records", ["provider_request_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_records_provider_request_id", table_name="usage_records")
    op.drop_index("ix_usage_records_provider_channel_id", table_name="usage_records")
    for column in (
        "raw_usage_json", "route_attempts_json", "price_version", "reasoning_tokens",
        "input_cache_miss_tokens", "input_cache_hit_tokens", "provider_request_id",
        "provider_channel_id", "provider_cost_micros",
    ):
        op.drop_column("usage_records", column)
    op.drop_column("model_channels", "provider_output_cost_micros_per_1k")
    op.drop_column("model_channels", "provider_input_cost_micros_per_1k")
    for table, column in (
        ("billing_accounts", "balance_micros"),
        ("api_keys", "spending_limit_micros"),
        ("api_keys", "spent_micros"),
        ("model_configs", "input_price_micros_per_1k"),
        ("model_configs", "output_price_micros_per_1k"),
        ("account_balance_transactions", "amount_micros"),
        ("payment_orders", "amount_micros"),
        ("redemption_codes", "amount_micros"),
        ("redemption_claims", "amount_micros"),
        ("usage_records", "amount_micros"),
    ):
        op.alter_column(table, column, existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=column == "spending_limit_micros")
