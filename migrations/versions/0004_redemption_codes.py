"""Add redemption codes and account redemption claims.

Revision ID: 0004_redemption_codes
Revises: 0003_model_channels
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_redemption_codes"
down_revision: Union[str, None] = "0003_model_channels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "redemption_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("code_prefix", sa.String(length=24), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("amount_micros", sa.Integer(), nullable=False),
        sa.Column("max_redemptions", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("redeemed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_redemption_codes_code_prefix", "redemption_codes", ["code_prefix"])
    op.create_index("ix_redemption_codes_code_hash", "redemption_codes", ["code_hash"], unique=True)
    op.create_index("ix_redemption_codes_active", "redemption_codes", ["active"])
    op.create_index("ix_redemption_codes_expires_at", "redemption_codes", ["expires_at"])
    op.create_table(
        "redemption_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("redemption_code_id", sa.Integer(), sa.ForeignKey("redemption_codes.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("billing_accounts.id"), nullable=False),
        sa.Column("amount_micros", sa.Integer(), nullable=False),
        sa.Column("reference_id", sa.String(length=120), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("redemption_code_id", "account_id", name="uq_redemption_claims_code_account"),
    )
    op.create_index("ix_redemption_claims_redemption_code_id", "redemption_claims", ["redemption_code_id"])
    op.create_index("ix_redemption_claims_account_id", "redemption_claims", ["account_id"])
    op.create_index("ix_redemption_claims_reference_id", "redemption_claims", ["reference_id"], unique=True)
    op.create_index("ix_redemption_claims_redeemed_at", "redemption_claims", ["redeemed_at"])


def downgrade() -> None:
    op.drop_table("redemption_claims")
    op.drop_table("redemption_codes")
