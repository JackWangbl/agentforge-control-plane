from app.access.auth import CurrentUser, get_current_user, require_permission, viewer
from app.access.kinds import PERMISSION_CATALOG, ResourceKind, ResourcePermission
from app.access.policy import DenyAllResourceAccessPolicy, TenantSharePolicy
from app.access.scope import kind_for, stamp_owner
from app.access.service import access_service

__all__ = [
    "CurrentUser",
    "DenyAllResourceAccessPolicy",
    "PERMISSION_CATALOG",
    "ResourceKind",
    "ResourcePermission",
    "TenantSharePolicy",
    "access_service",
    "get_current_user",
    "kind_for",
    "require_permission",
    "stamp_owner",
    "viewer",
]
