from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.access.kinds import ResourceKind, ResourcePermission


@dataclass(frozen=True)
class ResourceRef:
    kind: ResourceKind
    owner_id: int
    resource_id: int
    permission: ResourcePermission = ResourcePermission.READ


class ResourceAccessPolicyBase(ABC):
    """AgentScope-compatible extension point: map a viewer to cross-owner refs."""

    @abstractmethod
    def list_accessible(self, viewer, kind: ResourceKind, rows: list) -> list[ResourceRef]:
        raise NotImplementedError

    def can_edit(self, viewer, kind: ResourceKind, owner_id: int | None, resource_id: int, rows: list) -> bool:
        if owner_id is not None and owner_id == getattr(viewer, "id", None):
            return True
        return any(
            ref.kind == kind
            and ref.resource_id == resource_id
            and ref.permission == ResourcePermission.EDIT
            for ref in self.list_accessible(viewer, kind, rows)
        )


class DenyAllResourceAccessPolicy(ResourceAccessPolicyBase):
    def list_accessible(self, viewer, kind: ResourceKind, rows: list) -> list[ResourceRef]:
        return []


class TenantSharePolicy(ResourceAccessPolicyBase):
    """Same-tenant members may share resources; cross-tenant is always denied."""

    def list_accessible(self, viewer, kind: ResourceKind, rows: list) -> list[ResourceRef]:
        from app.access.auth import CurrentUser
        from app.access.kinds import RESOURCE_READ, RESOURCE_WRITE

        if not isinstance(viewer, CurrentUser) or not viewer.has(RESOURCE_READ[kind]):
            return []
        permission = ResourcePermission.EDIT if viewer.has(RESOURCE_WRITE[kind]) else ResourcePermission.READ
        refs: list[ResourceRef] = []
        for row in rows:
            tenant_id = getattr(row, "tenant_id", None)
            if tenant_id not in (None, viewer.tenant_id):
                continue
            owner_id = getattr(row, "owner_id", None)
            if owner_id == viewer.id:
                continue
            refs.append(ResourceRef(
                kind=kind,
                owner_id=owner_id or 0,
                resource_id=int(row.id),
                permission=permission,
            ))
        return refs
