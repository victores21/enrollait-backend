
# from __future__ import annotations

# from uuid import uuid4
# from fastapi import APIRouter, Depends, Form, UploadFile, File, HTTPException
# from sqlalchemy import text
# from sqlalchemy.orm import Session
# from pydantic import BaseModel

# from app.core.db import get_db
# from app.core.tenant import get_tenant_id_from_request
# from app.core.supabase import upload_product_image  # ✅ reuse same uploader


# router = APIRouter()


# # -----------------------------
# # Schemas
# # -----------------------------
# class TenantOut(BaseModel):
#     tenant_id: int
#     name: str
#     logo: str | None = None
#     primary_color: str | None = None


# class TenantPublicOut(BaseModel):
#     ok: bool
#     tenant: TenantOut

# def _ensure_tenants_domain(db: Session):
#     db.execute(text("alter table tenants add column if not exists domain text;"))
#     db.execute(text("""
#         create unique index if not exists tenants_domain_uniq
#         on tenants (lower(domain));
#     """))
#     db.commit()

# # -----------------------------
# # Supabase image helpers (same as your products)
# # -----------------------------
# def _ext_from_content_type(content_type: str) -> str:
#     ct = (content_type or "").lower()
#     if ct == "image/png":
#         return ".png"
#     if ct in ("image/jpeg", "image/jpg"):
#         return ".jpg"
#     if ct == "image/webp":
#         return ".webp"
#     return ""


# def _validate_image_bytes(image: UploadFile, data: bytes, max_mb: int = 5) -> None:
#     allowed = {"image/png", "image/jpeg", "image/webp"}
#     if not image.content_type or image.content_type.lower() not in allowed:
#         raise HTTPException(status_code=400, detail="logo must be png, jpg, or webp")

#     max_bytes = max_mb * 1024 * 1024
#     if len(data) > max_bytes:
#         raise HTTPException(status_code=400, detail=f"logo too large (max {max_mb}MB)")


# def _extract_public_url(res) -> str | None:
#     if isinstance(res, str):
#         return res
#     if isinstance(res, dict):
#         return res.get("public_url") or res.get("url") or res.get("publicUrl")
#     return None


# def _upload_to_supabase(image: UploadFile, data: bytes, key: str) -> str:
#     """
#     Keep compatibility with multiple helper signatures (same approach you use).
#     """
#     attempts = [
#         lambda: upload_product_image(image, key),
#         lambda: upload_product_image(image, key, image.content_type),
#         lambda: upload_product_image(data, key),
#         lambda: upload_product_image(data, key, image.content_type),
#     ]

#     last_err: Exception | None = None
#     for fn in attempts:
#         try:
#             res = fn()
#             url = _extract_public_url(res)
#             if not url:
#                 raise RuntimeError("upload_product_image did not return a public url")
#             return url
#         except TypeError as e:
#             last_err = e
#             continue

#     raise TypeError(f"upload_product_image signature mismatch. Last error: {last_err}")


# def _make_tenant_logo_key(tenant_id: int, content_type: str) -> str:
#     ext = _ext_from_content_type(content_type) or ".bin"
#     # You can choose any path structure you like:
#     return f"tenants/{tenant_id}/branding/logo/{uuid4().hex}{ext}"



# # -----------------------------
# # Routes
# # -----------------------------
# @router.get("/tenant", response_model=TenantPublicOut)
# def get_tenant_public_info(
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     db: Session = Depends(get_db),
# ):
#     row = db.execute(
#         text("""
#             select id, coalesce(name, ''), logo, primary_color
#               from tenants
#              where id = :t
#              limit 1
#         """),
#         {"t": int(tenant_id)},
#     ).fetchone()

#     if not row:
#         # This should be rare if get_tenant_id_from_request works correctly,
#         # but it's good to fail safely.
#         return {"ok": False, "tenant": {"tenant_id": int(tenant_id), "name": "", "logo": None}}

#     return {
#         "ok": True,
#         "tenant": {
#             "tenant_id": int(row[0]),
#             "name": str(row[1] or ""),
#             "logo": row[2],
#             "primary_color": row[3],
#         },
#     }


# @router.get("/tenant-id")
# def get_tenant_id(request: Request, db: Session = Depends(get_db)):
#     _ensure_tenants_domain(db)

#     host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].strip().lower()
#     if not host:
#         return {"ok": False, "message": "Missing Host header"}

#     row = db.execute(
#         text("select id from tenants where lower(domain) = :d limit 1"),
#         {"d": host},
#     ).fetchone()

#     if not row:
#         return {"ok": False, "message": f"No tenant configured for domain: {host}"}

#     return {"ok": True, "tenant_id": int(row[0])}



# # -----------------------------
# # PATCH /tenant
# # -----------------------------
# @router.patch("/tenant")
# def update_tenant_branding(
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     db: Session = Depends(get_db),
#     name: str | None = Form(None),
#     logo: UploadFile | None = File(None),
#     logo_url: str | None = Form(None),  # send "" to clear logo
# ):
#     # Load current tenant
#     current = db.execute(
#         text("select id, name, logo from tenants where id = :t limit 1"),
#         {"t": int(tenant_id)},
#     ).fetchone()

#     if not current:
#         raise HTTPException(status_code=404, detail="Tenant not found")

#     next_name = current[1]
#     next_logo = current[2]

#     # Update name (if provided)
#     if name is not None:
#         name_clean = (name or "").strip()
#         if name_clean == "":
#             raise HTTPException(status_code=400, detail="name cannot be empty")
#         next_name = name_clean

#     # Clear/set logo via URL (optional)
#     # - if logo_url == "" => clear logo
#     # - if logo_url != "" => store new URL
#     if logo_url is not None:
#         next_logo = (logo_url.strip() or None)

#     # Upload logo file to Supabase (wins over logo_url if both are provided)
#     if logo is not None:
#         data = logo.file.read()
#         _validate_image_bytes(logo, data, max_mb=5)

#         key = _make_tenant_logo_key(int(tenant_id), logo.content_type or "")
#         public_url = _upload_to_supabase(logo, data, key)
#         next_logo = public_url

#     # Persist
#     db.execute(
#         text("update tenants set name = :n, logo = :l where id = :t"),
#         {"n": next_name, "l": next_logo, "t": int(tenant_id)},
#     )
#     db.commit()

#     return {
#         "ok": True,
#         "tenant": {
#             "tenant_id": int(tenant_id),
#             "name": str(next_name or ""),
#             "logo": next_logo,
#         },
#     }

from __future__ import annotations

import re
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, UploadFile, File, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request
from app.core.supabase import upload_product_image  # ✅ reuse same uploader

router = APIRouter()

# ✅ hex color validator (#RRGGBB)
_hex = re.compile(r"^#([0-9a-fA-F]{6})$")


# -----------------------------
# Schemas
# -----------------------------
class TenantOut(BaseModel):
    tenant_id: int
    name: str
    logo: str | None = None
    primary_color: str | None = None


class TenantPublicOut(BaseModel):
    ok: bool
    tenant: TenantOut


# -----------------------------
# DB ensure helpers
# -----------------------------
def _ensure_tenants_domain(db: Session):
    db.execute(text("alter table tenants add column if not exists domain text;"))
    db.execute(
        text(
            """
        create unique index if not exists tenants_domain_uniq
        on tenants (lower(domain));
    """
        )
    )
    db.commit()


def _ensure_tenants_branding(db: Session):
    # ✅ make sure primary_color exists (safe in prod)
    db.execute(text("alter table tenants add column if not exists primary_color text;"))
    db.commit()


# -----------------------------
# Supabase image helpers (same as your products)
# -----------------------------
def _ext_from_content_type(content_type: str) -> str:
    ct = (content_type or "").lower()
    if ct == "image/png":
        return ".png"
    if ct in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if ct == "image/webp":
        return ".webp"
    return ""


def _validate_image_bytes(image: UploadFile, data: bytes, max_mb: int = 5) -> None:
    allowed = {"image/png", "image/jpeg", "image/webp"}
    if not image.content_type or image.content_type.lower() not in allowed:
        raise HTTPException(status_code=400, detail="logo must be png, jpg, or webp")

    max_bytes = max_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=400, detail=f"logo too large (max {max_mb}MB)"
        )


def _extract_public_url(res) -> str | None:
    if isinstance(res, str):
        return res
    if isinstance(res, dict):
        return res.get("public_url") or res.get("url") or res.get("publicUrl")
    return None


def _upload_to_supabase(image: UploadFile, data: bytes, key: str) -> str:
    """
    Keep compatibility with multiple helper signatures (same approach you use).
    """
    attempts = [
        lambda: upload_product_image(image, key),
        lambda: upload_product_image(image, key, image.content_type),
        lambda: upload_product_image(data, key),
        lambda: upload_product_image(data, key, image.content_type),
    ]

    last_err: Exception | None = None
    for fn in attempts:
        try:
            res = fn()
            url = _extract_public_url(res)
            if not url:
                raise RuntimeError("upload_product_image did not return a public url")
            return url
        except TypeError as e:
            last_err = e
            continue

    raise TypeError(f"upload_product_image signature mismatch. Last error: {last_err}")


def _make_tenant_logo_key(tenant_id: int, content_type: str) -> str:
    ext = _ext_from_content_type(content_type) or ".bin"
    return f"tenants/{tenant_id}/branding/logo/{uuid4().hex}{ext}"


# -----------------------------
# Routes
# -----------------------------
@router.get("/tenant", response_model=TenantPublicOut)
def get_tenant_public_info(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    _ensure_tenants_branding(db)

    row = db.execute(
        text(
            """
            select id, coalesce(name, ''), logo, primary_color
              from tenants
             where id = :t
             limit 1
        """
        ),
        {"t": int(tenant_id)},
    ).fetchone()

    if not row:
        return {
            "ok": False,
            "tenant": {
                "tenant_id": int(tenant_id),
                "name": "",
                "logo": None,
                "primary_color": None,
            },
        }

    return {
        "ok": True,
        "tenant": {
            "tenant_id": int(row[0]),
            "name": str(row[1] or ""),
            "logo": row[2],
            "primary_color": row[3],
        },
    }


@router.get("/tenant-id")
def get_tenant_id(request: Request, db: Session = Depends(get_db)):
    _ensure_tenants_domain(db)

    host = (
        (request.headers.get("x-forwarded-host") or request.headers.get("host") or "")
        .split(":")[0]
        .strip()
        .lower()
    )
    if not host:
        return {"ok": False, "message": "Missing Host header"}

    row = db.execute(
        text("select id from tenants where lower(domain) = :d limit 1"),
        {"d": host},
    ).fetchone()

    if not row:
        return {"ok": False, "message": f"No tenant configured for domain: {host}"}

    return {"ok": True, "tenant_id": int(row[0])}


# -----------------------------
# PATCH /tenant (Branding)
# - logo is REQUIRED (cannot be cleared)
# - logo can only be replaced (upload only)
# - primary_color saved to tenants.primary_color (hex like #F16D34)
# -----------------------------
@router.patch("/tenant")
def update_tenant_branding(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
    name: str = Form(...),
    primary_color: str | None = Form(None),
    logo: UploadFile | None = File(None),
):
    _ensure_tenants_branding(db)

    name_clean = (name or "").strip()
    if not name_clean:
        raise HTTPException(status_code=400, detail="name is required")

    # ✅ validate + normalize primary_color
    primary_clean: str | None = None
    if primary_color is not None:
        pc = primary_color.strip()
        if pc != "" and not _hex.match(pc):
            raise HTTPException(
                status_code=400, detail="primary_color must be hex like #F16D34"
            )
        primary_clean = pc if pc != "" else None

    # current
    current = db.execute(
        text("select id, name, logo, primary_color from tenants where id = :t limit 1"),
        {"t": int(tenant_id)},
    ).fetchone()

    if not current:
        raise HTTPException(status_code=404, detail="Tenant not found")

    existing_logo = current[2]

    # upload logo if provided
    logo_url: str | None = None
    if logo is not None:
        data = logo.file.read()
        _validate_image_bytes(logo, data, max_mb=5)
        key = _make_tenant_logo_key(int(tenant_id), logo.content_type or "")
        logo_url = _upload_to_supabase(logo, data, key)

    # ✅ logo is REQUIRED: if tenant has no logo and you didn't upload one => reject
    if not existing_logo and not logo_url:
        raise HTTPException(status_code=400, detail="logo is required and cannot be empty")

    updates: dict[str, object] = {"name": name_clean}

    # only update primary_color if field is present in request
    if primary_color is not None:
        updates["primary_color"] = primary_clean

    # only update logo if uploaded
    if logo_url:
        updates["logo"] = logo_url

    set_sql = ", ".join([f"{k} = :{k}" for k in updates.keys()])

    row = db.execute(
        text(
            f"""
            update tenants
               set {set_sql}
             where id = :t
         returning id, coalesce(name,''), logo, primary_color
        """
        ),
        {**updates, "t": int(tenant_id)},
    ).fetchone()

    db.commit()

    return {
        "ok": True,
        "tenant": {
            "tenant_id": int(row[0]),
            "name": str(row[1] or ""),
            "logo": row[2],
            "primary_color": row[3],
        },
    }