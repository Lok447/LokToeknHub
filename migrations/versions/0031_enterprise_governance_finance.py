"""add budget, concurrency, SCIM and finance state

Revision ID: 0031_enterprise_governance_finance
Revises: 0030_gateway_reliability
"""

from alembic import op
import sqlalchemy as sa


revision = "0031_enterprise_governance_finance"
down_revision = "0030_gateway_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("billing_accounts", sa.Column("budget_micros", sa.BigInteger(), nullable=True))
    op.add_column("billing_accounts", sa.Column("concurrency_limit", sa.Integer(), nullable=True))
    op.add_column("organizations", sa.Column("budget_micros", sa.BigInteger(), nullable=True))
    op.add_column("organizations", sa.Column("concurrency_limit", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("budget_micros", sa.BigInteger(), nullable=True))
    op.add_column("projects", sa.Column("concurrency_limit", sa.Integer(), nullable=True))
    op.add_column("api_keys", sa.Column("concurrency_limit", sa.Integer(), nullable=True))
    op.create_table("invoices", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("invoice_no", sa.String(64), nullable=False), sa.Column("account_id", sa.Integer(), sa.ForeignKey("billing_accounts.id"), nullable=False), sa.Column("amount_micros", sa.BigInteger(), nullable=False), sa.Column("currency", sa.String(12), nullable=False, server_default="CNY"), sa.Column("status", sa.String(24), nullable=False, server_default="requested"), sa.Column("tax_identity", sa.String(255)), sa.Column("file_url", sa.String(500)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("issued_at", sa.DateTime(timezone=True)))
    op.create_index("ix_invoices_invoice_no", "invoices", ["invoice_no"])
    op.create_index("ix_invoices_account_id", "invoices", ["account_id"])
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_table("revenue_shares", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("account_id", sa.Integer(), sa.ForeignKey("billing_accounts.id"), nullable=False), sa.Column("period", sa.String(7), nullable=False), sa.Column("gross_micros", sa.BigInteger(), nullable=False, server_default="0"), sa.Column("share_micros", sa.BigInteger(), nullable=False, server_default="0"), sa.Column("status", sa.String(24), nullable=False, server_default="calculated"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_revenue_shares_account_id", "revenue_shares", ["account_id"])
    op.create_index("ix_revenue_shares_period", "revenue_shares", ["period"])


def downgrade() -> None:
    op.drop_index("ix_revenue_shares_period", table_name="revenue_shares")
    op.drop_index("ix_revenue_shares_account_id", table_name="revenue_shares")
    op.drop_table("revenue_shares")
    op.drop_index("ix_invoices_status", table_name="invoices")
    op.drop_index("ix_invoices_account_id", table_name="invoices")
    op.drop_index("ix_invoices_invoice_no", table_name="invoices")
    op.drop_table("invoices")
    op.drop_column("api_keys", "concurrency_limit")
    op.drop_column("projects", "concurrency_limit")
    op.drop_column("projects", "budget_micros")
    op.drop_column("organizations", "concurrency_limit")
    op.drop_column("organizations", "budget_micros")
    op.drop_column("billing_accounts", "budget_micros")
    op.drop_column("billing_accounts", "concurrency_limit")
