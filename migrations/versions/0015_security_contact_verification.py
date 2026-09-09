"""Add security contact verification challenges.

Revision ID: 0015_security_contact_verification
Revises: 0014_normalize_token_price_unit
Create Date: 2026-08-19
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0015_security_contact_verification"
down_revision: str = "0014_normalize_token_price_unit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_contact_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("contact", sa.String(length=160), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["billing_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_security_contact_challenges_account_id", "security_contact_challenges", ["account_id"])
    op.create_index("ix_security_contact_challenges_token_hash", "security_contact_challenges", ["token_hash"])
    op.create_index("ix_security_contact_challenges_expires_at", "security_contact_challenges", ["expires_at"])
    op.create_index("ix_security_contact_challenges_consumed_at", "security_contact_challenges", ["consumed_at"])


def downgrade() -> None:
    op.drop_table("security_contact_challenges")
