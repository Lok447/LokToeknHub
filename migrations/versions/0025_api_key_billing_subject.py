"""track the billing subject separately from the key creator

Revision ID: 0025_api_key_billing_subject
Revises: 0024_trial_key_ownership
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_api_key_billing_subject"
down_revision = "0024_trial_key_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("billing_account_id", sa.Integer(), nullable=True))
    op.create_index("ix_api_keys_billing_account_id", "api_keys", ["billing_account_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_billing_account_id", table_name="api_keys")
    op.drop_column("api_keys", "billing_account_id")
