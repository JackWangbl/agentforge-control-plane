"""Cross-resource tenant checks performed before Agent configuration is persisted."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServer, SandboxPolicy, Skill


def validate_agent_bindings(
    db: Session,
    tenant_id: int,
    *,
    skill_ids: Optional[list[int]] = None,
    mcp_ids: Optional[list[int]] = None,
    sandbox_id: Optional[int] = None,
) -> None:
    """Reject references that do not exist inside the trusted tenant boundary."""

    _validate_ids(db, Skill, tenant_id, skill_ids, "Skill")
    _validate_ids(db, McpServer, tenant_id, mcp_ids, "MCP")
    if sandbox_id is not None:
        found = db.scalar(select(SandboxPolicy.id).where(
            SandboxPolicy.id == sandbox_id,
            SandboxPolicy.tenant_id == tenant_id,
        ))
        if found is None:
            raise HTTPException(422, "Sandbox 不存在或不属于当前租户")


def _validate_ids(
    db: Session,
    model: type,
    tenant_id: int,
    resource_ids: Optional[list[int]],
    label: str,
) -> None:
    if resource_ids is None:
        return
    wanted = {int(item) for item in resource_ids}
    if not wanted:
        return
    found = set(db.scalars(select(model.id).where(
        model.id.in_(wanted),
        model.tenant_id == tenant_id,
    )).all())
    if found != wanted:
        raise HTTPException(422, f"{label} 不存在或不属于当前租户")
