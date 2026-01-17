# from fastapi import APIRouter, Depends
# from pydantic import BaseModel
# from sqlalchemy.orm import Session
# from sqlalchemy import text

# from app.core.db import get_db

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


# @router.post("/tenants/{tenant_id}/stripe/config")
# def save_stripe_config(tenant_id: int, payload: StripeConfigPayload, db: Session = Depends(get_db)):
#     _ensure_tenants_stripe_columns(db)

#     sk = (payload.stripe_secret_key or "").strip()
#     whsec = (payload.stripe_webhook_secret or "").strip()
#     pk = (payload.stripe_publishable_key or None)
#     if pk is not None:
#         pk = pk.strip()

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


from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request  # ✅ add this

router = APIRouter()


class StripeConfigPayload(BaseModel):
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_publishable_key: str | None = None


def _ensure_tenants_stripe_columns(db: Session) -> None:
    # Safe even if columns already exist
    db.execute(text("alter table tenants add column if not exists stripe_secret_key text;"))
    db.execute(text("alter table tenants add column if not exists stripe_webhook_secret text;"))
    db.execute(text("alter table tenants add column if not exists stripe_publishable_key text;"))
    db.commit()


# ✅ CHANGE: removed /tenants/{tenant_id}/... and infer tenant from request
@router.post("/stripe/config")
def save_stripe_config(
    payload: StripeConfigPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),  # ✅ inferred tenant
    db: Session = Depends(get_db),
):
    _ensure_tenants_stripe_columns(db)

    sk = (payload.stripe_secret_key or "").strip()
    whsec = (payload.stripe_webhook_secret or "").strip()
    pk = payload.stripe_publishable_key.strip() if payload.stripe_publishable_key else None

    if not sk.startswith("sk_"):
        return {"ok": False, "message": "Invalid stripe_secret_key (must start with sk_)"}
    if not whsec.startswith("whsec_"):
        return {"ok": False, "message": "Invalid stripe_webhook_secret (must start with whsec_)"}

    updated = db.execute(
        text("""
            update tenants
               set stripe_secret_key = :sk,
                   stripe_webhook_secret = :whsec,
                   stripe_publishable_key = :pk
             where id = :id
            returning id
        """),
        {"id": tenant_id, "sk": sk, "whsec": whsec, "pk": pk},
    ).fetchone()

    db.commit()

    if not updated:
        return {"ok": False, "message": f"Tenant {tenant_id} not found"}

    return {"ok": True, "tenant_id": tenant_id}