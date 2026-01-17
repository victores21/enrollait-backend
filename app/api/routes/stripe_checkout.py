# from fastapi import APIRouter, Depends, Request
# from sqlalchemy.orm import Session
# from sqlalchemy import text
# import stripe

# from app.core.db import get_db
# from app.core.tenant import get_tenant_id_from_request  # your new tenant middleware

# router = APIRouter()

# def _get_tenant_stripe(db: Session, tenant_id: int) -> tuple[str | None, str | None, str | None]:
#     row = db.execute(
#         text("""
#             select stripe_secret_key, stripe_webhook_secret, stripe_publishable_key
#               from tenants
#              where id = :id
#              limit 1
#         """),
#         {"id": tenant_id},
#     ).fetchone()
#     if not row:
#         return (None, None, None)
#     return (row[0], row[1], row[2])

# def _get_base_url(req: Request) -> str:
#     # Prefer forwarded headers (prod) and fallback to host (local)
#     proto = req.headers.get("x-forwarded-proto") or "http"
#     host = req.headers.get("x-forwarded-host") or req.headers.get("host") or "localhost:3000"
#     return f"{proto}://{host}"

# @router.post("/stripe/checkout/session")
# async def create_checkout_session(
#     request: Request,
#     db: Session = Depends(get_db),
#     tenant_id: int = Depends(get_tenant_id_from_request),
# ):
#     body = await request.json()
#     product_id = body.get("product_id")
#     customer_email = body.get("customer_email") or None

#     if not product_id:
#         return {"ok": False, "message": "Missing product_id"}

#     # 1) Load tenant Stripe keys from DB
#     stripe_secret_key, _, _ = _get_tenant_stripe(db, tenant_id)
#     if not stripe_secret_key:
#         return {"ok": False, "message": "Stripe not configured for this tenant"}

#     # ✅ Python Stripe SDK: set api_key (NO stripe.Stripe(...))
#     stripe.api_key = stripe_secret_key

#     # 2) Load product from DB for this tenant
#     row = db.execute(
#         text("""
#             select id, title, description, image_url,
#                    price_cents, currency, discounted_price, price
#               from products
#              where tenant_id = :t and id = :pid
#              limit 1
#         """),
#         {"t": tenant_id, "pid": int(product_id)},
#     ).fetchone()

#     if not row:
#         return {"ok": False, "message": "Product not found"}

#     pid, title, description, image_url, price_cents, currency, discounted_price, price = row

#     # pick amount
#     unit_amount = int(price_cents or 0)
#     if discounted_price is not None:
#         # discounted_price numeric like 19.99 -> cents
#         unit_amount = int(round(float(discounted_price) * 100))

#     if unit_amount < 50:
#         return {"ok": False, "message": "Invalid price"}

#     currency = (currency or "usd").lower()

#     base_url = _get_base_url(request)
#     return_url = body.get("return_url") or f"{base_url}/success?session_id={{CHECKOUT_SESSION_ID}}"

#     meta = {
#         "tenant_id": str(tenant_id),
#         "product_id": str(pid),
#     }

#     session = stripe.checkout.Session.create(
#         ui_mode="embedded",
#         mode="payment",
#         customer_email=customer_email,
#         client_reference_id=f"{tenant_id}:{pid}",
#         line_items=[
#             {
#                 "quantity": 1,
#                 "price_data": {
#                     "unit_amount": unit_amount,
#                     "currency": currency,
#                     "product_data": {
#                         "name": title or f"Product {pid}",
#                         "description": description or None,
#                         "images": [image_url] if image_url else None,
#                     },
#                 },
#             }
#         ],
#         metadata=meta,
#         payment_intent_data={"metadata": meta},
#         return_url=return_url,
#     )

#     return {
#         "ok": True,
#         "id": session["id"],
#         "client_secret": session["client_secret"],
#     }

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
import stripe

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request

router = APIRouter()


def _get_tenant_stripe_and_domain(
    db: Session, tenant_id: int
) -> tuple[str | None, str | None, str | None, str | None]:
    row = db.execute(
        text("""
            select stripe_secret_key, stripe_webhook_secret, stripe_publishable_key, domain
              from tenants
             where id = :id
             limit 1
        """),
        {"id": tenant_id},
    ).fetchone()

    if not row:
        return (None, None, None, None)

    return (row[0], row[1], row[2], row[3])


def _frontend_base_url_from_domain(domain: str) -> str:
    """
    tenants.domain can be:
      - "app.example.com"
      - "https://app.example.com"
      - "http://localhost:3000"
    We'll normalize it to include protocol.
    """
    d = (domain or "").strip().rstrip("/")
    if not d:
        return ""

    if d.startswith("http://") or d.startswith("https://"):
        return d

    # default to https for real domains
    if d.startswith("localhost") or d.startswith("127.0.0.1"):
        return f"http://{d}"
    return f"https://{d}"


@router.post("/stripe/checkout/session")
async def create_checkout_session(
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_tenant_id_from_request),
):
    body = await request.json()
    product_id = body.get("product_id")
    customer_email = body.get("customer_email") or None

    if not product_id:
        return {"ok": False, "message": "Missing product_id"}

    # 1) Load tenant Stripe keys + tenant frontend domain from DB
    stripe_secret_key, _, _, domain = _get_tenant_stripe_and_domain(db, tenant_id)
    if not stripe_secret_key:
        return {"ok": False, "message": "Stripe not configured for this tenant"}

    frontend_base = _frontend_base_url_from_domain(domain or "")
    if not frontend_base:
        return {
            "ok": False,
            "message": "Tenant domain not configured (needed to build return_url)",
            "tenant_id": tenant_id,
        }

    stripe.api_key = stripe_secret_key

    # 2) Load product from DB for this tenant
    row = db.execute(
        text("""
            select id, title, description, image_url,
                   price_cents, currency, discounted_price
              from products
             where tenant_id = :t and id = :pid
             limit 1
        """),
        {"t": tenant_id, "pid": int(product_id)},
    ).fetchone()

    if not row:
        return {"ok": False, "message": "Product not found"}

    pid, title, description, image_url, price_cents, currency, discounted_price = row

    unit_amount = int(price_cents or 0)
    if discounted_price is not None:
        unit_amount = int(round(float(discounted_price) * 100))

    if unit_amount < 50:
        return {"ok": False, "message": "Invalid price"}

    currency = (currency or "usd").lower()

    # ✅ ALWAYS return to FRONTEND (tenant domain)
    return_url = body.get("return_url") or f"{frontend_base}/success?session_id={{CHECKOUT_SESSION_ID}}"

    meta = {
        "tenant_id": str(tenant_id),
        "product_id": str(pid),
    }

    session = stripe.checkout.Session.create(
        ui_mode="embedded",
        mode="payment",
        customer_email=customer_email,
        client_reference_id=f"{tenant_id}:{pid}",
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "unit_amount": unit_amount,
                    "currency": currency,
                    "product_data": {
                        "name": title or f"Product {pid}",
                        "description": description or None,
                        "images": [image_url] if image_url else None,
                    },
                },
            }
        ],
        metadata=meta,
        payment_intent_data={"metadata": meta},
        return_url=return_url,
    )

    return {
        "ok": True,
        "id": session["id"],
        "client_secret": session["client_secret"],
        "return_url": return_url,  # helpful while debugging
    }