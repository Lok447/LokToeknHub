"""add provider-level connections

Revision ID: 0018_provider_connections
Revises: 0017_alert_incidents_and_provider_bills
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_provider_connections"
down_revision = "0017_alert_incidents_and_provider_bills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("preset_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("provider_base_url", sa.String(length=500), nullable=False),
        sa.Column("provider_api_key_env", sa.String(length=120), nullable=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("default_input_price_micros_per_1k", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("default_output_price_micros_per_1k", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("discovered_model_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_model_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("callable_model_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("preset_id"),
    )
    op.create_index("ix_provider_connections_preset_id", "provider_connections", ["preset_id"], unique=True)
    op.create_index("ix_provider_connections_active", "provider_connections", ["active"])
    op.create_index("ix_provider_connections_status", "provider_connections", ["status"])
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("model_channels", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("provider_connection_id", sa.Integer(), nullable=True))
            batch_op.create_index("ix_model_channels_provider_connection_id", ["provider_connection_id"])
            batch_op.create_foreign_key(
                "fk_model_channels_provider_connection_id",
                "provider_connections",
                ["provider_connection_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.add_column("model_channels", sa.Column("provider_connection_id", sa.Integer(), nullable=True))
        op.create_index("ix_model_channels_provider_connection_id", "model_channels", ["provider_connection_id"])
        op.create_foreign_key(
            "fk_model_channels_provider_connection_id",
            "model_channels",
            "provider_connections",
            ["provider_connection_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("model_channels", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_model_channels_provider_connection_id", type_="foreignkey")
            batch_op.drop_index("ix_model_channels_provider_connection_id")
            batch_op.drop_column("provider_connection_id")
    else:
        op.drop_constraint("fk_model_channels_provider_connection_id", "model_channels", type_="foreignkey")
        op.drop_index("ix_model_channels_provider_connection_id", table_name="model_channels")
        op.drop_column("model_channels", "provider_connection_id")
    op.drop_index("ix_provider_connections_status", table_name="provider_connections")
    op.drop_index("ix_provider_connections_active", table_name="provider_connections")
    op.drop_index("ix_provider_connections_preset_id", table_name="provider_connections")
    op.drop_table("provider_connections")
