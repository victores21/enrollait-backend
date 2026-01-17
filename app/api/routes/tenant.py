from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.db import get_db

router = APIRouter()

def _ensure_tenants_domain(db: Session):
    db.execute(text("alter table tenants add column if not exists domain text;"))
    db.execute(text("""
        create unique index if not exists tenants_domain_uniq
        on tenants (lower(domain));
    """))
    db.commit()

@router.get("/tenant-id")
def get_tenant_id(request: Request, db: Session = Depends(get_db)):
    _ensure_tenants_domain(db)

    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].strip().lower()
    if not host:
        return {"ok": False, "message": "Missing Host header"}

    row = db.execute(
        text("select id from tenants where lower(domain) = :d limit 1"),
        {"d": host},
    ).fetchone()

    if not row:
        return {"ok": False, "message": f"No tenant configured for domain: {host}"}

    return {"ok": True, "tenant_id": int(row[0])}