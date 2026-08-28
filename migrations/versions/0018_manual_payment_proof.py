"""Add manual payment proof fields and rejected status support."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0018_manual_payment_proof"
down_revision: str | None = "0017_alert_incidents_and_provider_bills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payment_orders", sa.Column("payer_reference", sa.String(160), nullable=True))
    op.add_column("payment_orders", sa.Column("payer_note", sa.Text(), nullable=True))
    op.add_column("payment_orders", sa.Column("proof_submitted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_orders", "proof_submitted_at")
    op.drop_column("payment_orders", "payer_note")
    op.drop_column("payment_orders", "payer_reference")
