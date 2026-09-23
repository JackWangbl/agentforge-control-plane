from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access.auth import (
    CurrentUser,
    get_current_user,
    hash_password,
    new_token,
    require_permission,
    token_expiry,
    verify_password,
)
from app.access.kinds import PERMISSION_CATALOG
from app.database import get_db
from app.models import AuthToken, Role, Tenant, User
from app.schemas import LoginRequest, TenantCreate, UserCreate, UserUpdate

router = APIRouter()


def _dump_user(row: User, role: Role | None = None, tenant: Tenant | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "username": row.username,
        "display_name": row.display_name,
        "tenant_id": row.tenant_id,
        "tenant_name": tenant.name if tenant else "",
        "role_id": row.role_id,
        "role_name": role.name if role else "",
        "permissions": list((role.permissions if role else None) or []),
        "enabled": row.enabled,
        "created_at": row.created_at,
    }


@router.post("/api/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.username == payload.username.strip()))
    if user is None or not user.enabled or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    token = AuthToken(token=new_token(), user_id=user.id, expires_at=token_expiry())
    db.add(token)
    db.commit()
    role = db.get(Role, user.role_id) if user.role_id else None
    tenant = db.get(Tenant, user.tenant_id)
    return {"token": token.token, "user": _dump_user(user, role, tenant)}


@router.post("/api/auth/logout")
def logout(
    user: CurrentUser = Depends(get_current_user),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    del user
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if token:
        row = db.scalar(select(AuthToken).where(AuthToken.token == token))
        if row:
            db.delete(row)
            db.commit()
    return {"ok": True}


@router.get("/api/auth/me")
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    tenants = []
    if user.is_platform_admin:
        tenants = [
            {"id": row.id, "slug": row.slug, "name": row.name, "description": row.description}
            for row in db.scalars(select(Tenant).order_by(Tenant.id)).all()
        ]
    else:
        home = db.get(Tenant, user.home_tenant_id)
        if home:
            tenants = [{"id": home.id, "slug": home.slug, "name": home.name, "description": home.description}]
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "tenant_id": user.tenant_id,
        "home_tenant_id": user.home_tenant_id,
        "tenant_name": user.tenant_name,
        "role_id": user.role_id,
        "role_name": user.role_name,
        "permissions": user.permissions,
        "is_platform_admin": user.is_platform_admin,
        "tenants": tenants,
        "catalog": PERMISSION_CATALOG,
    }


@router.get("/api/tenants")
def list_tenants(user: CurrentUser = Depends(require_permission("tenant:admin", "user:read", "role:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(Tenant).order_by(Tenant.id)
    if not user.is_platform_admin:
        stmt = stmt.where(Tenant.id == user.tenant_id)
    return [
        {"id": row.id, "slug": row.slug, "name": row.name, "description": row.description, "status": row.status}
        for row in db.scalars(stmt).all()
    ]


@router.post("/api/tenants", status_code=201)
def create_tenant(payload: TenantCreate, user: CurrentUser = Depends(require_permission("platform:admin")), db: Session = Depends(get_db)) -> dict[str, Any]:
    del user
    slug = payload.slug.strip().lower()
    if db.scalar(select(Tenant).where(Tenant.slug == slug)):
        raise HTTPException(409, "租户标识已存在")
    row = Tenant(slug=slug, name=payload.name.strip(), description=payload.description or "")
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "slug": row.slug, "name": row.name, "description": row.description, "status": row.status}


@router.get("/api/users")
def list_users(user: CurrentUser = Depends(require_permission("user:read", "role:read", "tenant:admin")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(User).order_by(User.id.desc())
    if not user.is_platform_admin:
        stmt = stmt.where(User.tenant_id == user.tenant_id)
    rows = list(db.scalars(stmt).all())
    roles = {row.id: row for row in db.scalars(select(Role)).all()}
    tenants = {row.id: row for row in db.scalars(select(Tenant)).all()}
    return [_dump_user(row, roles.get(row.role_id or 0), tenants.get(row.tenant_id)) for row in rows]


@router.post("/api/users", status_code=201)
def create_user(payload: UserCreate, user: CurrentUser = Depends(require_permission("user:write", "tenant:admin")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.scalar(select(User).where(User.username == payload.username.strip())):
        raise HTTPException(409, "用户名已存在")
    tenant_id = payload.tenant_id or user.tenant_id
    if not user.is_platform_admin:
        tenant_id = user.tenant_id
    role = db.get(Role, payload.role_id)
    if role is None or (role.tenant_id not in (None, tenant_id) and not user.is_platform_admin):
        raise HTTPException(400, "角色不存在或不属于当前租户")
    row = User(
        tenant_id=tenant_id,
        username=payload.username.strip(),
        display_name=payload.display_name or payload.username,
        password_hash=hash_password(payload.password),
        role_id=payload.role_id,
        enabled=payload.enabled,
    )
    db.add(row)
    if role:
        role.user_count = (role.user_count or 0) + 1
    db.commit()
    db.refresh(row)
    return _dump_user(row, role, db.get(Tenant, tenant_id))


@router.put("/api/users/{user_id}")
def update_user(user_id: int, payload: UserUpdate, user: CurrentUser = Depends(require_permission("user:write", "tenant:admin")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(User, user_id)
    if row is None or (row.tenant_id != user.tenant_id and not user.is_platform_admin):
        raise HTTPException(404, "用户不存在")
    data = payload.model_dump(exclude_unset=True)
    if "password" in data:
        password = data.pop("password")
        if password:
            row.password_hash = hash_password(password)
    if "tenant_id" in data and not user.is_platform_admin:
        data.pop("tenant_id")
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    role = db.get(Role, row.role_id) if row.role_id else None
    if role:
        role.user_count = db.scalar(select(func.count()).select_from(User).where(User.role_id == role.id)) or 0
        db.commit()
    return _dump_user(row, role, db.get(Tenant, row.tenant_id))
