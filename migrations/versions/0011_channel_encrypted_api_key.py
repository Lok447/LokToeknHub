"""Add encrypted provider credentials for console-managed channels."""

from alembic import op
import sqlalchemy as sa


revision = "0011_channel_encrypted_api_key"
down_revision = "0010_channel_health_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_channels", sa.Column("encrypted_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_channels", "encrypted_api_key")
