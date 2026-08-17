"""Add API key expiration and spending controls.

Revision ID: 0002_api_key_controls
Revises: 0001_initial
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_api_key_controls"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("spending_limit_micros", sa.Integer(), nullable=True))
    op.add_column("api_keys", sa.Column("spent_micros", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("api_keys", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_api_keys_expires_at", "api_keys", ["expires_at"])
    op.execute(
        "UPDATE api_keys SET spent_micros = COALESCE((SELECT SUM(amount_micros) FROM usage_records "
        "WHERE usage_records.api_key_id = api_keys.id AND usage_records.status = 'success'), 0)"
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_expires_at", table_name="api_keys")
    op.drop_column("api_keys", "last_used_at")
    op.drop_column("api_keys", "spent_micros")
    op.drop_column("api_keys", "spending_limit_micros")
    op.drop_column("api_keys", "expires_at")
