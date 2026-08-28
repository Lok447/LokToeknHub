"""isolate api keys by trial access link

Revision ID: 0024_trial_key_ownership
Revises: 0023_account_access_modes_and_invitations
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_trial_key_ownership"
down_revision = "0023_account_access_modes_and_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("trial_token_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_api_keys_trial_token_hash", "api_keys", ["trial_token_hash"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_trial_token_hash", table_name="api_keys")
    op.drop_column("api_keys", "trial_token_hash")
