"""Add self-service portal credentials and trial-bound API key expiry.

Revision ID: 0006_portal_accounts_and_trial_key_expiry
Revises: 0005_audit_events
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_portal_accounts_and_trial_key_expiry"
down_revision: Union[str, None] = "0005_audit_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("billing_accounts", sa.Column("login_id", sa.String(length=160), nullable=True))
    op.add_column("billing_accounts", sa.Column("password_hash", sa.String(length=256), nullable=True))
    op.create_index("ix_billing_accounts_login_id", "billing_accounts", ["login_id"], unique=True)
    op.add_column("api_keys", sa.Column("trial_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_api_keys_trial_expires_at", "api_keys", ["trial_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_trial_expires_at", table_name="api_keys")
    op.drop_column("api_keys", "trial_expires_at")
    op.drop_index("ix_billing_accounts_login_id", table_name="billing_accounts")
    op.drop_column("billing_accounts", "password_hash")
    op.drop_column("billing_accounts", "login_id")
