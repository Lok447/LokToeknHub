"""add account access modes and invitation challenge purpose

Revision ID: 0023_account_access_modes_and_invitations
Revises: 0022_api_key_rate_limits
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_account_access_modes_and_invitations"
down_revision = "0022_api_key_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("billing_accounts", sa.Column("access_mode", sa.String(length=24), nullable=False, server_default="api"))
    op.create_index("ix_billing_accounts_access_mode", "billing_accounts", ["access_mode"])
    op.execute("UPDATE billing_accounts SET access_mode = 'portal' WHERE login_id IS NOT NULL AND login_id <> ''")
    op.add_column("password_reset_challenges", sa.Column("purpose", sa.String(length=24), nullable=False, server_default="password_reset"))
    op.create_index("ix_password_reset_challenges_purpose", "password_reset_challenges", ["purpose"])


def downgrade() -> None:
    op.drop_index("ix_password_reset_challenges_purpose", table_name="password_reset_challenges")
    op.drop_column("password_reset_challenges", "purpose")
    op.drop_index("ix_billing_accounts_access_mode", table_name="billing_accounts")
    op.drop_column("billing_accounts", "access_mode")
