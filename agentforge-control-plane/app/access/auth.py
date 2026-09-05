from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import hmac
import os
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db

current_user_var: ContextVar[Optional["CurrentUser"]] = ContextVar("current_user", default=None)

TOKEN_TTL_HOURS = 24 * 7
PBKDF_ROUNDS = 120_000


@dataclass
class CurrentUser:
    id: int
    username: str
    display_name: str
    tenant_id: int
    home_tenant_id: int
    tenant_name: str
    role_id: Optional[int]
    role_name: str
    permissions: list[str] = field(default_factory=list)

    @property
    def is_platform_admin(self) -> bool:
        return self.has("*", "platform:admin")

    def has(self, *perms: str) -> bool:
        granted = set(self.permissions or [])
        if "*" in granted or "platform:admin" in granted:
            return True
        for perm in perms:
            if perm in granted:
                return True
            if perm.endswith(":read"):
                write = perm[:-5] + ":write"
                if write in granted:
                    return True
            if perm in {"eval:read", "eval:write"} and "eval:run" in granted:
                return True
        return False


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF_ROUNDS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, expected = stored.split("$", 1)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF_ROUNDS)
    return hmac.compare_digest(digest.hex(), expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_expiry() -> datetime:
    return datetime.utcnow() + timedelta(hours=TOKEN_TTL_HOURS)


def build_current_user(user, role, tenant, tenant_id: int) -> CurrentUser:
    permissions = list((role.permissions if role else None) or [])
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        tenant_id=tenant_id,
        home_tenant_id=user.tenant_id,
        tenant_name=tenant.name if tenant else "",
        role_id=user.role_id,
        role_name=role.name if role else "",
        permissions=permissions,
    )


def load_user_by_id(db: Session, user_id: int, tenant_override: Optional[int] = None) -> CurrentUser:
    from app.models import Role, Tenant, User

    user = db.get(User, user_id)
    if user is None or not user.enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已失效")
    role = db.get(Role, user.role_id) if user.role_id else None
    actor = build_current_user(user, role, db.get(Tenant, user.tenant_id), user.tenant_id)
    if tenant_override and tenant_override != user.tenant_id:
        if not actor.is_platform_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "不能切换到其他租户")
        tenant = db.get(Tenant, tenant_override)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "租户不存在")
        actor.tenant_id = tenant.id
        actor.tenant_name = tenant.name
    return actor


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    db: Session = Depends(get_db),
) -> CurrentUser:
    from app.models import AuthToken

    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    tenant_override = int(x_tenant_id) if x_tenant_id and str(x_tenant_id).isdigit() else None
    if token:
        row = db.scalar(select(AuthToken).where(AuthToken.token == token))
        if row is None or row.expires_at < datetime.utcnow():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")
        actor = load_user_by_id(db, row.user_id, tenant_override)
        current_user_var.set(actor)
        return actor
    dev_user = os.environ.get("AUTH_DEV_USER", "").strip()
    if dev_user:
        from app.models import User

        user = db.scalar(select(User).where(User.username == dev_user))
        if user:
            actor = load_user_by_id(db, user.id, tenant_override)
            current_user_var.set(actor)
            return actor
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")


def require_permission(*perms: str):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if perms and not user.has(*perms):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "没有权限执行该操作")
        return user
    return dependency


def viewer() -> Optional[CurrentUser]:
    return current_user_var.get()
