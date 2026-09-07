"""merge the existing payment-proof and webhook-event migration heads

Revision ID: 0028_merge_heads
Revises: 0018_manual_payment_proof, 0027_payment_webhook_events
"""

from alembic import op


revision = "0028_merge_heads"
down_revision = ("0018_manual_payment_proof", "0027_payment_webhook_events")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
