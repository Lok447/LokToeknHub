"""persist payment webhook idempotency and replay state

Revision ID: 0027_payment_webhook_events
Revises: 0026_single_key_rotation
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_payment_webhook_events"
down_revision = "0026_single_key_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("order_no", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="received"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_payment_webhook_events_event_id"),
    )
    op.create_index("ix_payment_webhook_events_event_id", "payment_webhook_events", ["event_id"])
    op.create_index("ix_payment_webhook_events_order_no", "payment_webhook_events", ["order_no"])
    op.create_index("ix_payment_webhook_events_status", "payment_webhook_events", ["status"])
    op.create_index("ix_payment_webhook_events_received_at", "payment_webhook_events", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_webhook_events_received_at", table_name="payment_webhook_events")
    op.drop_index("ix_payment_webhook_events_status", table_name="payment_webhook_events")
    op.drop_index("ix_payment_webhook_events_order_no", table_name="payment_webhook_events")
    op.drop_index("ix_payment_webhook_events_event_id", table_name="payment_webhook_events")
    op.drop_table("payment_webhook_events")
