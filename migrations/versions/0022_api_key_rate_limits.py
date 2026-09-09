"""add per api key rate limit overrides"""

from alembic import op
import sqlalchemy as sa

revision = "0022_api_key_rate_limits"
down_revision = "0021_key_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("rate_limit_requests", sa.Integer(), nullable=True))
    op.add_column("api_keys", sa.Column("rate_limit_window_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "rate_limit_window_seconds")
    op.drop_column("api_keys", "rate_limit_requests")
