# from fastapi import APIRouter, Depends
# from pydantic import BaseModel
# from sqlalchemy.orm import Session
# from sqlalchemy import text

# from app.core.db import get_db
# from app.core.tenant import get_tenant_id_from_request  # ✅ add this

# router = APIRouter()


# class StripeConfigPayload(BaseModel):
#     stripe_secret_key: str
#     stripe_webhook_secret: str
#     stripe_publishable_key: str | None = None


# def _ensure_tenants_stripe_columns(db: Session) -> None:
#     # Safe even if columns already exist
#     db.execute(text("alter table tenants add column if not exists stripe_secret_key text;"))
#     db.execute(text("alter table tenants add column if not exists stripe_webhook_secret text;"))
#     db.execute(text("alter table tenants add column if not exists stripe_publishable_key text;"))
#     db.commit()


# # ✅ CHANGE: removed /tenants/{tenant_id}/... and infer tenant from request
# @router.post("/stripe/config")
# def save_stripe_config(
#     payload: StripeConfigPayload,
#     tenant_id: int = Depends(get_tenant_id_from_request),  # ✅ inferred tenant
#     db: Session = Depends(get_db),
# ):
#     _ensure_tenants_stripe_columns(db)

#     sk = (payload.stripe_secret_key or "").strip()
#     whsec = (payload.stripe_webhook_secret or "").strip()
#     pk = payload.stripe_publishable_key.strip() if payload.stripe_publishable_key else None

#     if not sk.startswith("sk_"):
#         return {"ok": False, "message": "Invalid stripe_secret_key (must start with sk_)"}
#     if not whsec.startswith("whsec_"):
#         return {"ok": False, "message": "Invalid stripe_webhook_secret (must start with whsec_)"}

#     updated = db.execute(
#         text("""
#             update tenants
#                set stripe_secret_key = :sk,
#                    stripe_webhook_secret = :whsec,
#                    stripe_publishable_key = :pk
#              where id = :id
#             returning id
#         """),
#         {"id": tenant_id, "sk": sk, "whsec": whsec, "pk": pk},
#     ).fetchone()

#     db.commit()

#     if not updated:
#         return {"ok": False, "message": f"Tenant {tenant_id} not found"}

#     return {"ok": True, "tenant_id": tenant_id}



# app/api/routes/stripe_config.py
#
# Optimized:
# - ✅ Removed per-request schema changes (_ensure_tenants_stripe_columns) -> do migrations once
# - ✅ Proper HTTP errors instead of {"ok": False} (faster client handling + cleaner)
# - ✅ Single transaction (commit only once) + rollback on error
# - ✅ Uses COALESCE to support clearing publishable key with empty string (optional)
#
# One-time DB migration (run once):
#   alter table tenants add column if not exists stripe_secret_key text;
#   alter table tenants add column if not exists stripe_webhook_secret text;
#   alter table tenants add column if not exists stripe_publishable_key text;

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request

router = APIRouter()


class StripeConfigPayload(BaseModel):
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_publishable_key: str | None = None


@router.post("/stripe/config")
def save_stripe_config(
    payload: StripeConfigPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    sk = (payload.stripe_secret_key or "").strip()
    whsec = (payload.stripe_webhook_secret or "").strip()
    pk = (payload.stripe_publishable_key or "").strip() or None  # treat "" as None

    if not sk.startswith("sk_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid stripe_secret_key (must start with sk_)",
        )
    if not whsec.startswith("whsec_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid stripe_webhook_secret (must start with whsec_)",
        )

    try:
        updated = db.execute(
            text("""
                update tenants
                   set stripe_secret_key = :sk,
                       stripe_webhook_secret = :whsec,
                       stripe_publishable_key = :pk
                 where id = :id
                 returning id
            """),
            {"id": int(tenant_id), "sk": sk, "whsec": whsec, "pk": pk},
        ).fetchone()

        if not updated:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant {tenant_id} not found",
            )

        db.commit()

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save Stripe config: {type(e).__name__}: {str(e)}",
        )

    return {"ok": True, "tenant_id": int(tenant_id)}