from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request
from app.core.admin_security import decode_admin_token


def require_admin(
    request: Request,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
) -> dict:
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = decode_admin_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    if payload.get("typ") != "admin":
        raise HTTPException(status_code=401, detail="Invalid token type")

    if int(payload.get("tid")) != int(tenant_id):
        # 🔒 prevents cross-tenant token usage
        raise HTTPException(status_code=403, detail="Wrong tenant")

    row = db.execute(
        text("""
            select id, email, role, is_active
              from tenant_admin_users
             where tenant_id = :t and id = :id
             limit 1
        """),
        {"t": int(tenant_id), "id": int(payload.get("uid"))},
    ).fetchone()

    if not row or not bool(row[3]):
        raise HTTPException(status_code=401, detail="User disabled or not found")

    return {
        "tenant_id": int(tenant_id),
        "admin_user_id": int(row[0]),
        "email": str(row[1]),
        "role": str(row[2]),
    }