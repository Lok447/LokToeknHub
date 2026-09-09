"""prevent concurrent duplicate API key rotations

Revision ID: 0026_single_key_rotation
Revises: 0025_api_key_billing_subject
"""

from alembic import op


revision = "0026_single_key_rotation"
down_revision = "0025_api_key_billing_subject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("uq_api_keys_rotated_from_key_id", "api_keys", ["rotated_from_key_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_api_keys_rotated_from_key_id", table_name="api_keys")
