"""add worker claim lease fields"""

from alembic import op
import sqlalchemy as sa


revision = "0032_worker_claims"
down_revision = "0031_enterprise_governance_finance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_tasks", sa.Column("worker_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generation_tasks", sa.Column("worker_claim_token", sa.String(64), nullable=True))
    op.create_index("ix_generation_tasks_worker_claimed_at", "generation_tasks", ["worker_claimed_at"])
    op.create_index("ix_generation_tasks_worker_claim_token", "generation_tasks", ["worker_claim_token"])


def downgrade() -> None:
    op.drop_index("ix_generation_tasks_worker_claim_token", table_name="generation_tasks")
    op.drop_index("ix_generation_tasks_worker_claimed_at", table_name="generation_tasks")
    op.drop_column("generation_tasks", "worker_claim_token")
    op.drop_column("generation_tasks", "worker_claimed_at")
