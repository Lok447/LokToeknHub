"""Add OIDC external identity mappings and login challenges.

Revision ID: 0008_oidc_external_identities
Revises: 0007_release_readiness_security
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_oidc_external_identities"
down_revision: Union[str, None] = "0007_release_readiness_security"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="oidc"),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["billing_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_external_identities_issuer_subject"),
    )
    op.create_index("ix_external_identities_account_id", "external_identities", ["account_id"])
    op.create_index("ix_external_identities_provider", "external_identities", ["provider"])
    op.create_index("ix_external_identities_issuer", "external_identities", ["issuer"])
    op.create_index("ix_external_identities_subject", "external_identities", ["subject"])
    op.create_table(
        "oidc_login_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index("ix_oidc_login_challenges_state_hash", "oidc_login_challenges", ["state_hash"])
    op.create_index("ix_oidc_login_challenges_expires_at", "oidc_login_challenges", ["expires_at"])
    op.create_index("ix_oidc_login_challenges_consumed_at", "oidc_login_challenges", ["consumed_at"])


def downgrade() -> None:
    op.drop_table("oidc_login_challenges")
    op.drop_table("external_identities")
