
from fastapi import Request, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.db import get_db

def _get_host(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host = host.split(",")[0].strip()
    host = host.split(":")[0].strip().lower()
    return host

def get_tenant_id_from_request(
    request: Request,
    db: Session = Depends(get_db),
) -> int:
    host = _get_host(request)
    if not host:
        raise HTTPException(status_code=400, detail="Missing Host header")

    # 1) Prefer tenant_domains
    row = db.execute(
        text("""
            select td.tenant_id
              from tenant_domains td
             where lower(td.host) = :h
             limit 1
        """),
        {"h": host},
    ).fetchone()

    if row:
        return int(row[0])

    # 2) Backwards compatibility: tenants.domain
    row = db.execute(
        text("select id from tenants where lower(domain) = :d limit 1"),
        {"d": host},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"No tenant configured for domain: {host}")

    return int(row[0])

