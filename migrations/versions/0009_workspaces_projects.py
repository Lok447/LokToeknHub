"""Add personal workspaces, organizations, projects, and attribution fields.

Revision ID: 0009_workspaces_projects
Revises: 0008_oidc_external_identities
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_workspaces_projects"
down_revision: Union[str, None] = "0008_oidc_external_identities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("owner_account_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_account_id"], ["billing_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])
    op.create_index("ix_organizations_owner_account_id", "organizations", ["owner_account_id"])
    op.create_index("ix_organizations_active", "organizations", ["active"])
    op.create_table(
        "organization_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["billing_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "account_id", name="uq_organization_members_org_account"),
    )
    op.create_index("ix_organization_members_organization_id", "organization_members", ["organization_id"])
    op.create_index("ix_organization_members_account_id", "organization_members", ["account_id"])
    op.create_index("ix_organization_members_role", "organization_members", ["role"])
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("workspace_type", sa.String(length=24), nullable=False, server_default="personal"),
        sa.Column("owner_account_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_account_id"], ["billing_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_account_id", "workspace_type", name="uq_workspaces_owner_type"),
        sa.UniqueConstraint("organization_id"),
    )
    op.create_index("ix_workspaces_workspace_type", "workspaces", ["workspace_type"])
    op.create_index("ix_workspaces_owner_account_id", "workspaces", ["owner_account_id"])
    op.create_index("ix_workspaces_organization_id", "workspaces", ["organization_id"])
    op.create_index("ix_workspaces_active", "workspaces", ["active"])
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
    )
    op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"])
    op.create_index("ix_projects_active", "projects", ["active"])

    op.add_column("api_keys", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_index("ix_api_keys_project_id", "api_keys", ["project_id"])
    for table in ("usage_records", "account_balance_transactions", "payment_orders"):
        op.add_column(table, sa.Column("workspace_id", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("project_id", sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])

    connection = op.get_bind()
    accounts = connection.execute(sa.text("SELECT id, name FROM billing_accounts ORDER BY id")).mappings().all()
    for account in accounts:
        workspace_id = connection.execute(sa.text(
            "SELECT id FROM workspaces WHERE owner_account_id = :account_id AND workspace_type = 'personal'"
        ), {"account_id": account["id"]}).scalar_one_or_none()
        if workspace_id is None:
            workspace_id = connection.execute(sa.text(
                "INSERT INTO workspaces (name, workspace_type, owner_account_id, active, created_at) "
                "VALUES (:name, 'personal', :account_id, 1, CURRENT_TIMESTAMP)"
            ), {"name": f"{account['name']} 的个人空间", "account_id": account["id"]}).lastrowid
            if workspace_id is None:
                workspace_id = connection.execute(sa.text(
                    "SELECT id FROM workspaces WHERE owner_account_id = :account_id AND workspace_type = 'personal'"
                ), {"account_id": account["id"]}).scalar_one()
        project_id = connection.execute(sa.text(
            "SELECT id FROM projects WHERE workspace_id = :workspace_id AND slug = 'default'"
        ), {"workspace_id": workspace_id}).scalar_one_or_none()
        if project_id is None:
            connection.execute(sa.text(
                "INSERT INTO projects (workspace_id, name, slug, active, created_at) "
                "VALUES (:workspace_id, '默认项目', 'default', 1, CURRENT_TIMESTAMP)"
            ), {"workspace_id": workspace_id})
            project_id = connection.execute(sa.text(
                "SELECT id FROM projects WHERE workspace_id = :workspace_id AND slug = 'default'"
            ), {"workspace_id": workspace_id}).scalar_one()
        connection.execute(sa.text("UPDATE api_keys SET project_id = :project_id WHERE account_id = :account_id AND project_id IS NULL"), {"project_id": project_id, "account_id": account["id"]})
        connection.execute(sa.text(
            "UPDATE usage_records SET project_id = :project_id, workspace_id = :workspace_id "
            "WHERE account_id = :account_id AND project_id IS NULL"
        ), {"project_id": project_id, "workspace_id": workspace_id, "account_id": account["id"]})
        connection.execute(sa.text(
            "UPDATE account_balance_transactions SET project_id = :project_id, workspace_id = :workspace_id "
            "WHERE account_id = :account_id AND project_id IS NULL"
        ), {"project_id": project_id, "workspace_id": workspace_id, "account_id": account["id"]})
        connection.execute(sa.text(
            "UPDATE payment_orders SET project_id = :project_id, workspace_id = :workspace_id "
            "WHERE account_id = :account_id AND project_id IS NULL"
        ), {"project_id": project_id, "workspace_id": workspace_id, "account_id": account["id"]})


def downgrade() -> None:
    for table in ("payment_orders", "account_balance_transactions", "usage_records"):
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_column(table, "project_id")
        op.drop_column(table, "workspace_id")
    op.drop_index("ix_api_keys_project_id", table_name="api_keys")
    op.drop_column("api_keys", "project_id")
    op.drop_table("projects")
    op.drop_table("workspaces")
    op.drop_table("organization_members")
    op.drop_table("organizations")
