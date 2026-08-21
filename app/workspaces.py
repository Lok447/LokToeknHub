"""Workspace ownership helpers shared by portal and gateway flows."""

import re
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import BillingAccount, Organization, OrganizationMember, Project, Workspace


def _slug(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (normalized or fallback)[:100]


def ensure_personal_workspace(db: Session, account: BillingAccount) -> Workspace:
    workspace = db.scalar(select(Workspace).where(
        Workspace.owner_account_id == account.id,
        Workspace.workspace_type == "personal",
    ))
    if workspace:
        return workspace
    workspace = Workspace(
        name=f"{account.name} 的个人空间",
        workspace_type="personal",
        owner_account_id=account.id,
    )
    db.add(workspace)
    db.flush()
    db.add(Project(workspace_id=workspace.id, name="默认项目", slug="default"))
    db.flush()
    return workspace


def ensure_default_project(db: Session, workspace: Workspace) -> Project:
    project = db.scalar(select(Project).where(Project.workspace_id == workspace.id, Project.slug == "default"))
    if project:
        return project
    project = Project(workspace_id=workspace.id, name="默认项目", slug="default")
    db.add(project)
    db.flush()
    return project


def accessible_workspaces(db: Session, account: BillingAccount) -> list[tuple[Workspace, str]]:
    personal = ensure_personal_workspace(db, account)
    rows: list[tuple[Workspace, str]] = [(personal, "owner")]
    organizations = db.execute(
        select(Workspace, OrganizationMember.role)
        .join(Organization, Workspace.organization_id == Organization.id)
        .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
        .where(
            OrganizationMember.account_id == account.id,
            Workspace.workspace_type == "organization",
            Workspace.active.is_(True),
            Organization.active.is_(True),
        )
        .order_by(Workspace.id)
    ).all()
    rows.extend(organizations)
    return rows


def workspace_access(db: Session, account: BillingAccount, workspace_id: int) -> tuple[Workspace, str]:
    workspace = db.get(Workspace, workspace_id)
    if not workspace or not workspace.active:
        raise HTTPException(status_code=404, detail="workspace not found")
    if workspace.workspace_type == "personal" and workspace.owner_account_id == account.id:
        return workspace, "owner"
    if workspace.organization_id:
        member = db.scalar(select(OrganizationMember).where(
            OrganizationMember.organization_id == workspace.organization_id,
            OrganizationMember.account_id == account.id,
        ))
        if member:
            return workspace, member.role
    raise HTTPException(status_code=403, detail="workspace access is not permitted")


def project_access(db: Session, account: BillingAccount, project_id: int) -> tuple[Project, Workspace, str]:
    project = db.get(Project, project_id)
    if not project or not project.active:
        raise HTTPException(status_code=404, detail="project not found")
    workspace, role = workspace_access(db, account, project.workspace_id)
    return project, workspace, role


def create_organization(db: Session, account: BillingAccount, name: str) -> tuple[Organization, Workspace, Project]:
    base_slug = _slug(name, "organization")
    slug = base_slug
    while db.scalar(select(Organization.id).where(Organization.slug == slug)) is not None:
        slug = f"{base_slug[:88]}-{uuid.uuid4().hex[:8]}"
    organization = Organization(name=name.strip(), slug=slug, owner_account_id=account.id)
    db.add(organization)
    db.flush()
    db.add(OrganizationMember(organization_id=organization.id, account_id=account.id, role="admin"))
    workspace = Workspace(name=name.strip(), workspace_type="organization", organization_id=organization.id)
    db.add(workspace)
    db.flush()
    project = Project(workspace_id=workspace.id, name="默认项目", slug="default")
    db.add(project)
    db.flush()
    return organization, workspace, project


def create_project(db: Session, workspace: Workspace, name: str, slug: str | None) -> Project:
    project_slug = _slug(slug or name, "project")
    if db.scalar(select(Project.id).where(Project.workspace_id == workspace.id, Project.slug == project_slug)):
        raise HTTPException(status_code=409, detail="project slug already exists in workspace")
    project = Project(workspace_id=workspace.id, name=name.strip(), slug=project_slug)
    db.add(project)
    db.flush()
    return project


def require_workspace_manager(role: str) -> None:
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="workspace administrator role is required")


def workspace_data(db: Session, workspace: Workspace, role: str) -> dict[str, object]:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "type": workspace.workspace_type,
        "organization_id": workspace.organization_id,
        "role": role,
        "project_count": db.scalar(select(func.count(Project.id)).where(
            Project.workspace_id == workspace.id,
            Project.active.is_(True),
        )) or 0,
    }
