"""Add multiple upstream channels per public model.

Revision ID: 0003_model_channels
Revises: 0002_api_key_controls
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_model_channels"
down_revision: Union[str, None] = "0002_api_key_controls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_config_id", sa.Integer(), sa.ForeignKey("model_configs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("provider_base_url", sa.String(length=500), nullable=False),
        sa.Column("upstream_model", sa.String(length=120), nullable=False),
        sa.Column("provider_api_key_env", sa.String(length=120), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("circuit_open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("model_config_id", "name", name="uq_model_channels_model_name"),
    )
    op.create_index("ix_model_channels_model_config_id", "model_channels", ["model_config_id"])
    op.create_index("ix_model_channels_priority", "model_channels", ["priority"])
    op.create_index("ix_model_channels_active", "model_channels", ["active"])
    op.create_index("ix_model_channels_status", "model_channels", ["status"])
    op.create_index("ix_model_channels_circuit_open_until", "model_channels", ["circuit_open_until"])
    op.execute(
        "INSERT INTO model_channels "
        "(model_config_id, name, provider_base_url, upstream_model, provider_api_key_env, priority, weight, "
        "active, status, consecutive_failures, created_at) "
        "SELECT id, 'Primary', provider_base_url, upstream_model, provider_api_key_env, 100, 100, "
        "active, 'unknown', 0, CURRENT_TIMESTAMP FROM model_configs"
    )


def downgrade() -> None:
    op.drop_table("model_channels")
