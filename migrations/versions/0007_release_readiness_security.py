"""Add administrator sessions, user security lifecycle, and order reviews.

Revision ID: 0007_release_readiness_security
Revises: 0006_portal_accounts_and_trial_key_expiry
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_release_readiness_security"
down_revision: Union[str, None] = "0006_portal_accounts_and_trial_key_expiry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("billing_accounts", sa.Column("security_contact", sa.String(length=160), nullable=True))
    op.add_column("billing_accounts", sa.Column("security_contact_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("billing_accounts", sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("api_keys", sa.Column("rotated_from_key_id", sa.Integer(), nullable=True))
    op.create_index("ix_api_keys_rotated_from_key_id", "api_keys", ["rotated_from_key_id"])
    op.add_column("payment_orders", sa.Column("reviewed_by_admin_id", sa.Integer(), nullable=True))
    op.add_column("payment_orders", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payment_orders", sa.Column("review_note", sa.Text(), nullable=True))
    op.create_index("ix_payment_orders_reviewed_by_admin_id", "payment_orders", ["reviewed_by_admin_id"])

    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login_id", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="operator"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("login_id"),
    )
    op.create_index("ix_admin_users_login_id", "admin_users", ["login_id"])
    op.create_index("ix_admin_users_role", "admin_users", ["role"])
    op.create_index("ix_admin_users_active", "admin_users", ["active"])

    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_admin_sessions_admin_user_id", "admin_sessions", ["admin_user_id"])
    op.create_index("ix_admin_sessions_token_hash", "admin_sessions", ["token_hash"])
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])
    op.create_index("ix_admin_sessions_revoked_at", "admin_sessions", ["revoked_at"])

    op.create_table(
        "password_reset_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["billing_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_password_reset_challenges_account_id", "password_reset_challenges", ["account_id"])
    op.create_index("ix_password_reset_challenges_token_hash", "password_reset_challenges", ["token_hash"])
    op.create_index("ix_password_reset_challenges_expires_at", "password_reset_challenges", ["expires_at"])
    op.create_index("ix_password_reset_challenges_consumed_at", "password_reset_challenges", ["consumed_at"])

    op.create_table(
        "security_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["billing_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_notifications_account_id", "security_notifications", ["account_id"])
    op.create_index("ix_security_notifications_event_type", "security_notifications", ["event_type"])
    op.create_index("ix_security_notifications_read_at", "security_notifications", ["read_at"])
    op.create_index("ix_security_notifications_created_at", "security_notifications", ["created_at"])


def downgrade() -> None:
    op.drop_table("security_notifications")
    op.drop_table("password_reset_challenges")
    op.drop_table("admin_sessions")
    op.drop_table("admin_users")
    op.drop_index("ix_payment_orders_reviewed_by_admin_id", table_name="payment_orders")
    op.drop_column("payment_orders", "review_note")
    op.drop_column("payment_orders", "reviewed_at")
    op.drop_column("payment_orders", "reviewed_by_admin_id")
    op.drop_index("ix_api_keys_rotated_from_key_id", table_name="api_keys")
    op.drop_column("api_keys", "rotated_from_key_id")
    op.drop_column("billing_accounts", "session_version")
    op.drop_column("billing_accounts", "security_contact_verified_at")
    op.drop_column("billing_accounts", "security_contact")
