from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.access.auth import CurrentUser, viewer
from app.access.kinds import ROUTE_KIND, ResourceKind
from app.access.service import access_service


def stamp_owner(row: Any, user: CurrentUser) -> Any:
    if hasattr(row, "tenant_id"):
        row.tenant_id = user.tenant_id
    if hasattr(row, "owner_id"):
        row.owner_id = user.id
    return row


def kind_for(resource: str) -> ResourceKind:
    kind = ROUTE_KIND.get(resource)
    if kind is None:
        raise HTTPException(404, "Unknown resource")
    return kind


def attach_access(data: dict, row: Any, user: CurrentUser | None = None) -> dict:
    actor = user or viewer()
    if actor is None:
        return data
    kind = None
    table = getattr(getattr(row, "__table__", None), "name", "")
    mapping = {
        "agents": ResourceKind.AGENT,
        "model_configs": ResourceKind.CREDENTIAL,
        "mcp_servers": ResourceKind.MCP,
        "skills": ResourceKind.SKILL,
        "workflows": ResourceKind.WORKFLOW,
        "sandbox_policies": ResourceKind.SANDBOX,
        "datasets": ResourceKind.DATASET,
        "evaluation_runs": ResourceKind.EVALUATION,
        "experiments": ResourceKind.EXPERIMENT,
        "conversations": ResourceKind.SESSION,
        "traces": ResourceKind.TRACE,
        "roles": ResourceKind.ROLE,
        "users": ResourceKind.USER,
    }
    kind = mapping.get(table)
    if kind:
        data["editable"] = access_service.can_edit(actor, kind, row)
    data["tenant_id"] = getattr(row, "tenant_id", actor.tenant_id)
    data["owner_id"] = getattr(row, "owner_id", None)
    return data
