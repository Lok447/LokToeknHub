import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


def record_audit_event(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str | int,
    details: dict[str, Any] | None = None,
) -> None:
    """Queue a privacy-safe audit record in the caller's current transaction."""
    db.add(AuditEvent(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details_json=json.dumps(details, ensure_ascii=False, separators=(",", ":"), sort_keys=True) if details else None,
    ))
