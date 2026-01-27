# app/api/routes/admin_users.py
from __future__ import annotations

import os
from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request
from app.core.admin_security import hash_password

router = APIRouter(prefix="/admin/users", tags=["Admin Users"])


class BootstrapAdminPayload(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None  # optional


def _check_password_strength(pw: str) -> None:
    if len(pw or "") < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    # bcrypt limit: 72 bytes
    if len((pw or "").encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="password too long (max 72 bytes)")


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
def bootstrap_admin(
    payload: BootstrapAdminPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
    x_bootstrap_secret: str | None = Header(default=None),
):
    """
    Create FIRST tenant admin user for current tenant.
    Protected by ADMIN_BOOTSTRAP_SECRET.
    Only allowed if tenant has 0 tenant_admin_users yet.
    """
    secret_env = (os.getenv("ADMIN_BOOTSTRAP_SECRET") or "").strip()
    provided = (x_bootstrap_secret or "").strip()
    if not secret_env or provided != secret_env:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bootstrap secret")

    _check_password_strength(payload.password)

    # only allow bootstrap if no admins exist yet for this tenant
    existing = db.execute(
        text("select 1 from tenant_admin_users where tenant_id = :t limit 1"),
        {"t": int(tenant_id)},
    ).fetchone()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Admin already exists for this tenant")

    email = payload.email.strip().lower()
    name = (payload.name or "").strip() or None
    pw_hash = hash_password(payload.password)

    try:
        row = db.execute(
            text("""
                insert into tenant_admin_users
                    (tenant_id, email, password_hash, name, role, is_active, created_at)
                values
                    (:t, :email, :ph, :name, 'owner', true, now())
                returning
                    id, tenant_id, email, name, role, is_active, created_at, last_login_at
            """),
            {"t": int(tenant_id), "email": email, "ph": pw_hash, "name": name},
        ).fetchone()
        db.commit()

    except IntegrityError as ie:
        db.rollback()
        msg = str(getattr(ie, "orig", ie))
        raise HTTPException(status_code=409, detail={"message": "Admin with that email already exists", "error": msg})

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"message": "DB error creating admin", "error": f"{type(e).__name__}: {str(e)}"},
        )

    return {
        "ok": True,
        "admin": {
            "id": int(row[0]),
            "tenant_id": int(row[1]),
            "email": str(row[2]),
            "name": row[3],
            "role": str(row[4]),
            "is_active": bool(row[5]),
            "created_at": str(row[6]),
            "last_login_at": str(row[7]) if row[7] else None,
        },
    }