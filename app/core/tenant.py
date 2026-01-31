
# from fastapi import Request, Depends, HTTPException
# from sqlalchemy.orm import Session
# from sqlalchemy import text
# from app.core.db import get_db

# def _get_host(request: Request) -> str:
#     host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
#     host = host.split(",")[0].strip()
#     host = host.split(":")[0].strip().lower()
#     return host

# def get_tenant_id_from_request(
#     request: Request,
#     db: Session = Depends(get_db),
# ) -> int:
#     host = _get_host(request)
#     if not host:
#         raise HTTPException(status_code=400, detail="Missing Host header")

#     # 1) Prefer tenant_domains
#     row = db.execute(
#         text("""
#             select td.tenant_id
#               from tenant_domains td
#              where lower(td.host) = :h
#              limit 1
#         """),
#         {"h": host},
#     ).fetchone()

#     if row:
#         return int(row[0])

#     # 2) Backwards compatibility: tenants.domain
#     row = db.execute(
#         text("select id from tenants where lower(domain) = :d limit 1"),
#         {"d": host},
#     ).fetchone()

#     if not row:
#         raise HTTPException(status_code=404, detail=f"No tenant configured for domain: {host}")

#     return int(row[0])


# app/core/tenant.py
from __future__ import annotations

import re
import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db

router = APIRouter()

# ✅ Avoid running DDL on every request (big perf win)
_TABLES_ENSURED = False
_TABLES_LOCK = threading.Lock()


# -----------------------------
# Host / tenant resolution
# -----------------------------
def _get_host(request: Request) -> str:
	host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
	host = host.split(",")[0].strip()
	host = host.split(":")[0].strip().lower()
	return host


def _normalize_host(host: str) -> str:
	h = (host or "").strip().lower()
	h = re.sub(r"^https?://", "", h)
	h = h.split("/")[0]
	h = h.split(":")[0]
	return h


def get_tenant_id_from_request(
	request: Request,
	db: Session = Depends(get_db),
) -> int:
	host = _get_host(request)
	if not host:
		raise HTTPException(status_code=400, detail="Missing Host header")

	# ✅ single round-trip instead of 2 queries
	row = db.execute(
		text(
			"""
			select coalesce(
			  (select td.tenant_id from tenant_domains td where lower(td.host) = :h limit 1),
			  (select t.id from tenants t where lower(t.domain) = :h limit 1)
			) as tenant_id
			"""
		),
		{"h": host},
	).fetchone()

	tenant_id = row[0] if row else None
	if tenant_id is None:
		raise HTTPException(status_code=404, detail=f"No tenant configured for domain: {host}")

	return int(tenant_id)


# -----------------------------
# Ensure tables (dev only safety)
# -----------------------------
def _ensure_tables_once(db: Session) -> None:
	"""
	✅ Runs at most once per process.
	In production you should migrate schema (Alembic) and remove this entirely.
	"""
	global _TABLES_ENSURED
	if _TABLES_ENSURED:
		return

	with _TABLES_LOCK:
		if _TABLES_ENSURED:
			return

		# Keep minimal DDL (your DB already has these)
		db.execute(
			text(
				"""
				create table if not exists tenants (
				  id bigserial primary key,
				  name text not null default 'default',
				  moodle_url text,
				  moodle_token text,
				  created_at timestamptz not null default now(),
				  stripe_secret_key text,
				  stripe_webhook_secret text,
				  stripe_publishable_key text,
				  domain text
				);
				"""
			)
		)
		db.execute(
			text(
				"""
				create table if not exists tenant_domains (
				  id bigserial primary key,
				  tenant_id bigint not null references tenants(id) on delete cascade,
				  host text not null unique,
				  created_at timestamptz not null default now()
				);
				"""
			)
		)
		db.execute(
			text(
				"""
				create table if not exists tenant_admin_users (
				  id bigserial primary key,
				  tenant_id bigint not null references tenants(id) on delete cascade,
				  email text not null,
				  password_hash text not null,
				  name text,
				  role text not null default 'owner',
				  is_active boolean not null default true,
				  created_at timestamptz not null default now(),
				  last_login_at timestamptz
				);
				"""
			)
		)
		db.commit()
		_TABLES_ENSURED = True


# -----------------------------
# Create tenant endpoint
# -----------------------------
class TenantCreateBody(BaseModel):
	name: str = Field(..., min_length=2, max_length=120)
	domain: str = Field(..., min_length=3, max_length=255, description="Host only, e.g. acme.example.com")

	# Optional integrations
	moodle_url: Optional[str] = None
	moodle_token: Optional[str] = None
	stripe_secret_key: Optional[str] = None
	stripe_publishable_key: Optional[str] = None
	stripe_webhook_secret: Optional[str] = None

	# Optional initial admin (already hashed password)
	admin_email: Optional[EmailStr] = None
	admin_password_hash: Optional[str] = None
	admin_name: Optional[str] = None


@router.post("/tenants")
def create_tenant(body: TenantCreateBody, db: Session = Depends(get_db)):
	# ✅ runs at most once per process (huge performance improvement vs per-request DDL)
	_ensure_tables_once(db)

	host = _normalize_host(body.domain)
	if not host or "." not in host:
		raise HTTPException(status_code=400, detail="Invalid domain/host")

	# ✅ fastest path:
	# - do NOT pre-check domain with a SELECT
	# - rely on UNIQUE(host) constraint and catch IntegrityError (saves a query)
	try:
		# One transaction, one commit
		row = db.execute(
			text(
				"""
				insert into tenants
					(name, domain, moodle_url, moodle_token,
					 stripe_secret_key, stripe_publishable_key, stripe_webhook_secret)
				values
					(:name, :domain, :moodle_url, :moodle_token,
					 :ssk, :spk, :swh)
				returning id
				"""
			),
			{
				"name": body.name.strip(),
				"domain": host,  # keep for backwards compatibility
				"moodle_url": body.moodle_url.strip() if body.moodle_url else None,
				"moodle_token": body.moodle_token.strip() if body.moodle_token else None,
				"ssk": body.stripe_secret_key.strip() if body.stripe_secret_key else None,
				"spk": body.stripe_publishable_key.strip() if body.stripe_publishable_key else None,
				"swh": body.stripe_webhook_secret.strip() if body.stripe_webhook_secret else None,
			},
		).fetchone()

		if not row:
			raise HTTPException(status_code=500, detail="Failed to create tenant")

		tenant_id = int(row[0])

		# will raise IntegrityError if host already exists (UNIQUE)
		db.execute(
			text("insert into tenant_domains (tenant_id, host) values (:t, :h)"),
			{"t": tenant_id, "h": host},
		)

		if body.admin_email and body.admin_password_hash:
			db.execute(
				text(
					"""
					insert into tenant_admin_users (tenant_id, email, password_hash, name, role, is_active)
					values (:t, :e, :ph, :n, 'owner', true)
					"""
				),
				{
					"t": tenant_id,
					"e": str(body.admin_email).strip().lower(),
					"ph": body.admin_password_hash,
					"n": body.admin_name.strip() if body.admin_name else None,
				},
			)

		db.commit()

		return {"ok": True, "tenant_id": tenant_id, "name": body.name.strip(), "domain": host}

	except IntegrityError:
		# Most commonly: tenant_domains.host unique violation
		db.rollback()
		raise HTTPException(status_code=409, detail="Domain already in use")
	except HTTPException:
		db.rollback()
		raise
	except Exception as e:
		db.rollback()
		raise HTTPException(status_code=500, detail=f"Create tenant failed: {type(e).__name__}: {str(e)}")