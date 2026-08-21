"""add model pricing margin strategy

Revision ID: 0020_model_pricing_margin
Revises: 0019_provider_balance_snapshots
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_model_pricing_margin"
down_revision = "0019_provider_balance_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_configs", sa.Column("pricing_margin_bps", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("model_configs", "pricing_margin_bps")
