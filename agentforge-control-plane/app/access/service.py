from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.access.auth import CurrentUser
from app.access.kinds import RESOURCE_READ, RESOURCE_WRITE, ResourceKind, ResourcePermission
from app.access.policy import ResourceAccessPolicyBase, ResourceRef, TenantSharePolicy

KIND_MODELS: dict[ResourceKind, type] = {}


def bind_kind_models() -> dict[ResourceKind, type]:
    if KIND_MODELS:
        return KIND_MODELS
    from app.models import (
        Agent,
        Conversation,
        Dataset,
        EvaluationRun,
        McpServer,
        ModelConfig,
        Role,
        SandboxPolicy,
        Skill,
        Trace,
        User,
        Workflow,
    )

    KIND_MODELS.update({
        ResourceKind.AGENT: Agent,
        ResourceKind.CREDENTIAL: ModelConfig,
        ResourceKind.MCP: McpServer,
        ResourceKind.SKILL: Skill,
        ResourceKind.WORKFLOW: Workflow,
        ResourceKind.SANDBOX: SandboxPolicy,
        ResourceKind.DATASET: Dataset,
        ResourceKind.EVALUATION: EvaluationRun,
        ResourceKind.SESSION: Conversation,
        ResourceKind.TRACE: Trace,
        ResourceKind.ROLE: Role,
        ResourceKind.USER: User,
    })
    return KIND_MODELS


class ResourceAccessService:
    """Owner-scoped reads plus policy-granted same-tenant refs (AgentScope model)."""

    def __init__(self, policy: ResourceAccessPolicyBase | None = None) -> None:
        self._policy = policy or TenantSharePolicy()

    def tenant_clause(self, model, user: CurrentUser):
        tenant_id = getattr(model, "tenant_id", None)
        if tenant_id is None:
            return True
        return or_(model.tenant_id == user.tenant_id, model.tenant_id.is_(None))

    def list_rows(self, user: CurrentUser, kind: ResourceKind, db: Session) -> list:
        if not user.has(RESOURCE_READ[kind]):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "没有权限查看该资源")
        model = bind_kind_models()[kind]
        rows = list(db.scalars(select(model).where(self.tenant_clause(model, user)).order_by(model.id.desc())).all())
        own = [row for row in rows if getattr(row, "owner_id", None) in (None, user.id)]
        seen = {int(row.id) for row in own}
        refs = self._policy.list_accessible(user, kind, rows)
        visible = list(own)
        by_id = {int(row.id): row for row in rows}
        for ref in refs:
            if ref.resource_id in seen:
                continue
            row = by_id.get(ref.resource_id)
            if row is None:
                continue
            visible.append(row)
            seen.add(ref.resource_id)
        return visible

    def get_row(self, user: CurrentUser, kind: ResourceKind, resource_id: int, db: Session):
        if not user.has(RESOURCE_READ[kind]):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")
        model = bind_kind_models()[kind]
        row = db.get(model, resource_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")
        tenant_id = getattr(row, "tenant_id", user.tenant_id)
        if tenant_id not in (None, user.tenant_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")
        owner_id = getattr(row, "owner_id", None)
        if owner_id in (None, user.id):
            return row
        refs = self._policy.list_accessible(user, kind, [row])
        if any(ref.resource_id == int(row.id) for ref in refs):
            return row
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")

    def resolve_for_edit(self, user: CurrentUser, kind: ResourceKind, resource_id: int, db: Session):
        row = self.get_row(user, kind, resource_id, db)
        if not self.can_edit(user, kind, row):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "该资源对当前用户只读")
        return row

    def can_edit(self, user: CurrentUser, kind: ResourceKind, row) -> bool:
        if not user.has(RESOURCE_WRITE[kind]):
            return False
        owner_id = getattr(row, "owner_id", None)
        if owner_id in (None, user.id):
            return True
        return self._policy.can_edit(user, kind, owner_id, int(row.id), [row])

    def can_view_secret(self, user: CurrentUser, row) -> bool:
        owner_id = getattr(row, "owner_id", None)
        return owner_id in (None, user.id) or user.has("tenant:admin", "*")


access_service = ResourceAccessService()
