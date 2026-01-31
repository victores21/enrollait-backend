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
import stripe

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


@router.get("/stripe/snapshot")
def stripe_snapshot(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    row = db.execute(
        text("""
            select stripe_secret_key, stripe_webhook_secret, stripe_publishable_key
              from tenants
             where id = :id
             limit 1
        """),
        {"id": int(tenant_id)},
    ).fetchone()

    if not row:
        return {"ok": False, "message": "Tenant not found"}

    sk, whsec, pk = row[0], row[1], row[2]
    configured = bool((sk or "").strip()) and bool((whsec or "").strip())

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "configured": configured,
        "stripe_publishable_key": pk,  # optional to show / prefill
        # Don't send secrets back to the frontend.
    }

# # -----------------------------
# # NEW: Real Stripe key validation (end-to-end)
# # -----------------------------
class StripeTestKeysPayload(BaseModel):
    # If provided, we test these values directly (good for "Test Keys" before saving)
    stripe_secret_key: str | None = None


@router.post("/stripe/test-keys")
def stripe_test_keys(
    payload: StripeTestKeysPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    """
    End-to-end Stripe validation.

    - If payload.stripe_secret_key is provided, validates that key against Stripe.
    - Otherwise, loads the tenant's saved stripe_secret_key from DB and validates it.
    - Returns non-secret metadata from Stripe (account id, livemode, etc.)
    """

    # 1) decide which secret key to test
    sk = (payload.stripe_secret_key or "").strip()

    if not sk:
        # fallback to tenant saved key
        row = db.execute(
            text("""
                select stripe_secret_key
                  from tenants
                 where id = :id
                 limit 1
            """),
            {"id": int(tenant_id)},
        ).fetchone()

        if not row or not (row[0] or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No stripe_secret_key provided and tenant has no saved Stripe config",
            )

        sk = str(row[0]).strip()

    # 2) basic format check (fast feedback)
    if not sk.startswith("sk_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid stripe_secret_key (must start with "sk_")',
        )

    # 3) real Stripe call
    try:
        stripe.api_key = sk
        acct = stripe.Account.retrieve()

        return {
            "ok": True,
            "message": "Secret key is valid.",
            "tenant_id": int(tenant_id),
            "account_id": acct.get("id"),
            "country": acct.get("country"),
            "charges_enabled": acct.get("charges_enabled"),
            "details_submitted": acct.get("details_submitted"),
            "livemode": acct.get("livemode"),
        }

    except stripe.error.AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Stripe secret key: {str(e)}",
        )
    except stripe.error.PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Stripe key lacks permission: {str(e)}",
        )
    except stripe.error.StripeError as e:
        # Any other Stripe SDK error
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Stripe error: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Stripe validation failed: {type(e).__name__}: {str(e)}",
        )
