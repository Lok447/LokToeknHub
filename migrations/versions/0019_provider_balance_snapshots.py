"""add provider balance snapshots and connection balance state

Revision ID: 0019_provider_balance_snapshots
Revises: 0018_provider_connections
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_provider_balance_snapshots"
down_revision = "0018_provider_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_connections", sa.Column("balance_micros", sa.BigInteger(), nullable=True))
    op.add_column("provider_connections", sa.Column("balance_currency", sa.String(length=12), nullable=True))
    op.add_column("provider_connections", sa.Column("balance_status", sa.String(length=24), nullable=False, server_default="unknown"))
    op.add_column("provider_connections", sa.Column("balance_source", sa.String(length=32), nullable=True))
    op.add_column("provider_connections", sa.Column("balance_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("provider_connections", sa.Column("balance_error", sa.Text(), nullable=True))
    op.add_column("provider_connections", sa.Column("balance_alert_threshold_micros", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_index("ix_provider_connections_balance_status", "provider_connections", ["balance_status"])
    op.create_table(
        "provider_balance_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_connection_id", sa.Integer(), sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount_micros", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=12), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_provider_balance_snapshots_provider_connection_id", "provider_balance_snapshots", ["provider_connection_id"])
    op.create_index("ix_provider_balance_snapshots_status", "provider_balance_snapshots", ["status"])
    op.create_index("ix_provider_balance_snapshots_checked_at", "provider_balance_snapshots", ["checked_at"])


def downgrade() -> None:
    op.drop_index("ix_provider_balance_snapshots_checked_at", table_name="provider_balance_snapshots")
    op.drop_index("ix_provider_balance_snapshots_status", table_name="provider_balance_snapshots")
    op.drop_index("ix_provider_balance_snapshots_provider_connection_id", table_name="provider_balance_snapshots")
    op.drop_table("provider_balance_snapshots")
    op.drop_index("ix_provider_connections_balance_status", table_name="provider_connections")
    op.drop_column("provider_connections", "balance_alert_threshold_micros")
    op.drop_column("provider_connections", "balance_error")
    op.drop_column("provider_connections", "balance_checked_at")
    op.drop_column("provider_connections", "balance_source")
    op.drop_column("provider_connections", "balance_status")
    op.drop_column("provider_connections", "balance_currency")
    op.drop_column("provider_connections", "balance_micros")
