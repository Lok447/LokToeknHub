"""Track whether a channel health result came from Mock or a real provider.

Revision ID: 0010_channel_health_source
Revises: 0009_workspaces_projects
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_channel_health_source"
down_revision: Union[str, None] = "0009_workspaces_projects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("model_channels", sa.Column("health_source", sa.String(length=24), nullable=False, server_default="unknown"))
    op.create_index("ix_model_channels_health_source", "model_channels", ["health_source"])


def downgrade() -> None:
    op.drop_index("ix_model_channels_health_source", table_name="model_channels")
    op.drop_column("model_channels", "health_source")
