from __future__ import annotations

import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request
from app.core.admin_security import hash_password, verify_password, create_admin_token
from app.core.admin_auth import require_admin

router = APIRouter()


class AdminLoginPayload(BaseModel):
    email: EmailStr
    password: str


class AdminBootstrapPayload(BaseModel):
    email: EmailStr
    password: str
    bootstrap_secret: str
    name: str | None = None


def _cookie_kwargs(request: Request) -> dict:
    secure_env = os.getenv("ADMIN_COOKIE_SECURE", "false").lower() == "true"
    # if behind a proxy you can also check x-forwarded-proto
    return {
        "httponly": True,
        "secure": bool(secure_env),
        "samesite": os.getenv("ADMIN_COOKIE_SAMESITE", "lax"),
        "path": "/",
    }


@router.post("/admin/auth/bootstrap")
def bootstrap_first_admin(
    payload: AdminBootstrapPayload,
    request: Request,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    expected = os.getenv("ADMIN_BOOTSTRAP_SECRET", "")
    if not expected or payload.bootstrap_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid bootstrap secret")

    count = db.execute(
        text("select count(*) from tenant_admin_users where tenant_id = :t"),
        {"t": int(tenant_id)},
    ).scalar() or 0
    if int(count) > 0:
        raise HTTPException(status_code=409, detail="Admin already exists for this tenant")

    email = str(payload.email).strip().lower()
    pw = payload.password or ""
    if len(pw) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    pw_hash = hash_password(pw)
    name = (payload.name or "").strip() or None

    row = db.execute(
        text("""
            insert into tenant_admin_users
              (tenant_id, email, password_hash, name, role, is_active, created_at)
            values
              (:t, :e, :ph, :name, 'owner', true, now())
            returning id, email, role
        """),
        {"t": int(tenant_id), "e": email, "ph": pw_hash, "name": name},
    ).fetchone()
    db.commit()

    admin_id = int(row[0])
    role = str(row[2])

    token = create_admin_token(tenant_id=tenant_id, admin_user_id=admin_id, email=email, role=role)

    jr = JSONResponse({"ok": True, "tenant_id": int(tenant_id), "admin_user_id": admin_id, "email": email, "role": role})
    jr.set_cookie(os.getenv("ADMIN_COOKIE_NAME", "admin_token"), token, **_cookie_kwargs(request))
    return jr


@router.post("/admin/auth/login")
def admin_login(
    payload: AdminLoginPayload,
    request: Request,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    email = str(payload.email).strip().lower()
    pw = payload.password or ""

    row = db.execute(
        text("""
            select id, password_hash, role, is_active
              from tenant_admin_users
             where tenant_id = :t and lower(email) = lower(:e)
             limit 1
        """),
        {"t": int(tenant_id), "e": email},
    ).fetchone()

    if not row or not bool(row[3]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    admin_id = int(row[0])
    pw_hash = str(row[1])
    role = str(row[2])

    if not verify_password(pw, pw_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    db.execute(
        text("update tenant_admin_users set last_login_at = now() where tenant_id = :t and id = :id"),
        {"t": int(tenant_id), "id": admin_id},
    )
    db.commit()

    token = create_admin_token(tenant_id=tenant_id, admin_user_id=admin_id, email=email, role=role)

    jr = JSONResponse({"ok": True, "tenant_id": int(tenant_id), "admin_user_id": admin_id, "email": email, "role": role})
    jr.set_cookie(os.getenv("ADMIN_COOKIE_NAME", "admin_token"), token, **_cookie_kwargs(request))
    return jr


@router.get("/admin/auth/me")
def admin_me(ctx: dict = Depends(require_admin)):
    return {"ok": True, **ctx}


@router.post("/admin/auth/logout")
def admin_logout():
    jr = JSONResponse({"ok": True})
    jr.delete_cookie(os.getenv("ADMIN_COOKIE_NAME", "admin_token"), path="/")
    return jr