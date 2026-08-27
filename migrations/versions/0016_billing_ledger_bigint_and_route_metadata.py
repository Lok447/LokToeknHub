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


money_columns = (
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
)


def alter_money_columns(source_type: sa.types.TypeEngine, target_type: sa.types.TypeEngine) -> None:
    """Use table recreation for SQLite, which cannot alter column types in place."""
    for table, column in money_columns:
        kwargs = {
            "existing_type": source_type,
            "type_": target_type,
            "existing_nullable": column == "spending_limit_micros",
        }
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(table, recreate="always") as batch_op:
                batch_op.alter_column(column, **kwargs)
        else:
            op.alter_column(table, column, **kwargs)


def upgrade() -> None:
    # One yuan is one million micros. INTEGER would cap a balance at about
    # 2,147 yuan in PostgreSQL, which is not sufficient for team accounts.
    alter_money_columns(sa.Integer(), sa.BigInteger())

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
    alter_money_columns(sa.BigInteger(), sa.Integer())
