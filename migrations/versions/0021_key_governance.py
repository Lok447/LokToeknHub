"""add key revocation, idempotency and credential source metadata"""

from alembic import op
import sqlalchemy as sa

revision = "0021_key_governance"
down_revision = "0020_model_pricing_margin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("revoke_reason", sa.String(length=255), nullable=True))
    op.add_column("api_keys", sa.Column("idempotency_key", sa.String(length=120), nullable=True))
    op.create_index("ix_api_keys_revoked_at", "api_keys", ["revoked_at"])
    op.create_index("ix_api_keys_idempotency_key", "api_keys", ["idempotency_key"], unique=True)
    op.add_column("provider_connections", sa.Column("credential_source", sa.String(length=24), nullable=False, server_default="environment"))
    op.add_column("model_channels", sa.Column("credential_source", sa.String(length=24), nullable=False, server_default="environment"))
    op.execute("UPDATE provider_connections SET credential_source = 'console' WHERE encrypted_api_key IS NOT NULL")
    op.execute("UPDATE model_channels SET credential_source = 'console' WHERE encrypted_api_key IS NOT NULL")


def downgrade() -> None:
    op.drop_column("model_channels", "credential_source")
    op.drop_column("provider_connections", "credential_source")
    op.drop_index("ix_api_keys_idempotency_key", table_name="api_keys")
    op.drop_index("ix_api_keys_revoked_at", table_name="api_keys")
    op.drop_column("api_keys", "idempotency_key")
    op.drop_column("api_keys", "revoke_reason")
    op.drop_column("api_keys", "revoked_at")
