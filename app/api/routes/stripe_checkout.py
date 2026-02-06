# from fastapi import APIRouter, Depends, Request
# from sqlalchemy.orm import Session
# from sqlalchemy import text
# import stripe

# from app.core.db import get_db
# from app.core.tenant import get_tenant_id_from_request

# router = APIRouter()


# # -----------------------------
# # Tenant helpers
# # -----------------------------
# def _get_tenant_stripe_keys(db: Session, tenant_id: int) -> tuple[str | None, str | None, str | None]:
#     row = db.execute(
#         text(
#             """
#             select stripe_secret_key, stripe_webhook_secret, stripe_publishable_key
#               from tenants
#              where id = :id
#              limit 1
#             """
#         ),
#         {"id": int(tenant_id)},
#     ).fetchone()

#     if not row:
#         return (None, None, None)

#     return (row[0], row[1], row[2])


# def _get_tenant_primary_host(db: Session, tenant_id: int) -> str | None:
#     """
#     Returns the host for a tenant from tenant_domains.host.
#     If you support multiple domains per tenant, this picks the earliest created one.
#     """
#     row = db.execute(
#         text(
#             """
#             select host
#               from tenant_domains
#              where tenant_id = :tid
#              order by created_at asc, id asc
#              limit 1
#             """
#         ),
#         {"tid": int(tenant_id)},
#     ).fetchone()

#     return str(row[0]).strip() if row and row[0] else None


# def _get_tenants_domain_fallback(db: Session, tenant_id: int) -> str | None:
#     """
#     Optional fallback: if you still want to support tenants.domain.
#     """
#     row = db.execute(
#         text(
#             """
#             select domain
#               from tenants
#              where id = :id
#              limit 1
#             """
#         ),
#         {"id": int(tenant_id)},
#     ).fetchone()

#     return str(row[0]).strip() if row and row[0] else None


# def _frontend_base_url_from_host(host: str) -> str:
#     """
#     Accepts:
#       - "school.example.com"
#       - "localhost:3000"
#       - "127.0.0.1:3000"
#       - "https://school.example.com"
#       - "http://localhost:3000"
#     Returns a normalized base URL without trailing slash.
#     """
#     h = (host or "").strip().rstrip("/")
#     if not h:
#         return ""

#     # If already includes scheme, trust it.
#     if h.startswith("http://") or h.startswith("https://"):
#         return h

#     # Local/dev hosts -> http
#     if h.startswith("localhost") or h.startswith("127.0.0.1"):
#         return f"http://{h}"

#     # Everything else -> https
#     return f"https://{h}"


# # -----------------------------
# # Endpoint
# # -----------------------------
# @router.post("/stripe/checkout/session")
# async def create_checkout_session(
#     request: Request,
#     db: Session = Depends(get_db),
#     tenant_id: int = Depends(get_tenant_id_from_request),
# ):
#     body = await request.json()
#     product_id = body.get("product_id")
#     customer_email = (body.get("customer_email") or "").strip().lower() or None

#     if not product_id:
#         return {"ok": False, "message": "Missing product_id"}

#     stripe_secret_key, _, _ = _get_tenant_stripe_keys(db, tenant_id)
#     if not stripe_secret_key:
#         return {"ok": False, "message": "Stripe not configured for this tenant", "tenant_id": tenant_id}

#     # ✅ Build frontend base from tenant_domains.host
#     host = _get_tenant_primary_host(db, tenant_id)

#     # Optional fallback to tenants.domain if no host exists
#     if not host:
#         host = _get_tenants_domain_fallback(db, tenant_id)

#     frontend_base = _frontend_base_url_from_host(host or "")
#     if not frontend_base:
#         return {
#             "ok": False,
#             "message": "Tenant host not configured (needed to build return_url). Add a row in tenant_domains.",
#             "tenant_id": tenant_id,
#         }

#     # Load product (tenant-scoped)
#     row = db.execute(
#         text(
#             """
#             select id, title, description, image_url,
#                    price_cents, currency, discounted_price
#               from products
#              where tenant_id = :t and id = :pid
#              limit 1
#             """
#         ),
#         {"t": int(tenant_id), "pid": int(product_id)},
#     ).fetchone()

#     if not row:
#         return {"ok": False, "message": "Product not found", "tenant_id": tenant_id}

#     pid, title, description, image_url, price_cents, currency, discounted_price = row

#     unit_amount = int(price_cents or 0)
#     if discounted_price is not None:
#         unit_amount = int(round(float(discounted_price) * 100))

#     if unit_amount < 50:
#         return {"ok": False, "message": "Invalid price", "tenant_id": tenant_id}

#     currency = (currency or "usd").lower()

#     # Always return to FRONTEND (tenant host)
#     return_url = body.get("return_url") or f"{frontend_base}/thank-you?session_id={{CHECKOUT_SESSION_ID}}"

#     try:
#         # 1) Create order first
#         order_row = db.execute(
#             text(
#                 """
#                 insert into orders (tenant_id, product_id, buyer_email, status, created_at)
#                 values (:t, :p, :e, 'pending', now())
#                 returning id
#                 """
#             ),
#             {"t": int(tenant_id), "p": int(pid), "e": customer_email},
#         ).fetchone()
#         order_id = int(order_row[0])

#         stripe.api_key = stripe_secret_key

#         meta = {
#             "tenant_id": str(tenant_id),
#             "product_id": str(pid),
#             "order_id": str(order_id),
#         }

#         # 2) Create Stripe session
#         session_kwargs = dict(
#             ui_mode="embedded",
#             mode="payment",
#             client_reference_id=str(order_id),
#             line_items=[
#                 {
#                     "quantity": 1,
#                     "price_data": {
#                         "unit_amount": unit_amount,
#                         "currency": currency,
#                         "product_data": {
#                             "name": title or f"Product {pid}",
#                             "description": description or None,
#                             "images": [image_url] if image_url else None,
#                         },
#                     },
#                 }
#             ],
#             metadata=meta,
#             payment_intent_data={"metadata": meta},
#             return_url=return_url,
#         )

#         if customer_email:
#             session_kwargs["customer_email"] = customer_email

#         session = stripe.checkout.Session.create(**session_kwargs)

#         # 3) Persist stripe_session_id
#         db.execute(
#             text(
#                 """
#                 update orders
#                    set stripe_session_id = :sid
#                  where id = :oid
#                 """
#             ),
#             {"sid": str(session["id"]), "oid": int(order_id)},
#         )

#         db.commit()

#         return {
#             "ok": True,
#             "tenant_id": tenant_id,
#             "order_id": order_id,
#             "id": session["id"],
#             "client_secret": session["client_secret"],
#             "return_url": return_url,
#             "frontend_base": frontend_base,
#         }

#     except Exception as e:
#         db.rollback()
#         return {"ok": False, "message": f"{type(e).__name__}: {str(e)}", "tenant_id": tenant_id}

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from decimal import Decimal, ROUND_HALF_UP
import re
import stripe

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request

router = APIRouter()


# -----------------------------
# Tenant helpers
# -----------------------------
def _get_tenant_stripe_keys(db: Session, tenant_id: int) -> tuple[str | None, str | None, str | None]:
    row = db.execute(
        text(
            """
            select stripe_secret_key, stripe_webhook_secret, stripe_publishable_key
              from tenants
             where id = :id
             limit 1
            """
        ),
        {"id": int(tenant_id)},
    ).fetchone()

    if not row:
        return (None, None, None)

    return (row[0], row[1], row[2])


def _get_tenant_primary_host(db: Session, tenant_id: int) -> str | None:
    row = db.execute(
        text(
            """
            select host
              from tenant_domains
             where tenant_id = :tid
             order by created_at asc, id asc
             limit 1
            """
        ),
        {"tid": int(tenant_id)},
    ).fetchone()

    return str(row[0]).strip() if row and row[0] else None


def _get_tenants_domain_fallback(db: Session, tenant_id: int) -> str | None:
    row = db.execute(
        text(
            """
            select domain
              from tenants
             where id = :id
             limit 1
            """
        ),
        {"id": int(tenant_id)},
    ).fetchone()

    return str(row[0]).strip() if row and row[0] else None


def _normalize_host(host: str) -> str:
    """
    Converts:
      - "https://school.com/"
      - "http://localhost:3000/path"
      - "school.com/"
    -> "school.com" or "localhost:3000"
    """
    h = (host or "").strip()

    # remove scheme if present
    h = re.sub(r"^https?://", "", h, flags=re.IGNORECASE)

    # strip path/query/fragment
    h = h.split("/")[0].split("?")[0].split("#")[0].strip()

    return h.rstrip("/")


def _frontend_base_url_from_host(host: str) -> str:
    """
    Always returns a base URL without trailing slash.
    - localhost / 127.0.0.1 => http
    - everything else => https
    """
    h = _normalize_host(host)
    if not h:
        return ""

    if h.startswith("localhost") or h.startswith("127.0.0.1"):
        return f"http://{h}"

    return f"https://{h}"


# -----------------------------
# Endpoint
# -----------------------------
@router.post("/stripe/checkout/session")
async def create_checkout_session(
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_tenant_id_from_request),
):
    body = await request.json()
    product_id = body.get("product_id")
    customer_email = (body.get("customer_email") or "").strip().lower() or None

    if not product_id:
        return {"ok": False, "message": "Missing product_id"}

    stripe_secret_key, _, _ = _get_tenant_stripe_keys(db, tenant_id)
    if not stripe_secret_key:
        return {
            "ok": False,
            "message": "Stripe not configured for this tenant",
            "tenant_id": tenant_id,
        }

    # ✅ Build frontend base from tenant_domains.host first
    host = _get_tenant_primary_host(db, tenant_id)

    # Optional fallback to tenants.domain if no host exists
    if not host:
        host = _get_tenants_domain_fallback(db, tenant_id)

    frontend_base = _frontend_base_url_from_host(host or "")
    if not frontend_base:
        return {
            "ok": False,
            "message": "Tenant host not configured (needed to build return_url). Add a row in tenant_domains.",
            "tenant_id": tenant_id,
        }

    # ✅ Always build return_url from tenant host (do NOT trust body return_url)
    return_url = f"{frontend_base}/thank-you?session_id={{CHECKOUT_SESSION_ID}}"

    # Load product (tenant-scoped)
    row = db.execute(
        text(
            """
            select id, title, description, image_url,
                   price_cents, currency, discounted_price
              from products
             where tenant_id = :t and id = :pid
             limit 1
            """
        ),
        {"t": int(tenant_id), "pid": int(product_id)},
    ).fetchone()

    if not row:
        return {"ok": False, "message": "Product not found", "tenant_id": tenant_id}

    pid, title, description, image_url, price_cents, currency, discounted_price = row

    # Price in cents
    unit_amount = int(price_cents or 0)

    # ✅ safer conversion for discounted_price (numeric -> cents) without float rounding
    if discounted_price is not None:
        d = Decimal(str(discounted_price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        unit_amount = int(d * 100)

    if unit_amount < 50:
        return {"ok": False, "message": "Invalid price", "tenant_id": tenant_id}

    currency = (currency or "usd").lower()

    try:
        # 1) Create order first
        # ✅ store total_cents now so your /orders/paged shows amount immediately
        order_row = db.execute(
            text(
                """
                insert into orders (tenant_id, product_id, buyer_email, status, created_at, total_cents)
                values (:t, :p, :e, 'pending', now(), :total)
                returning id
                """
            ),
            {
                "t": int(tenant_id),
                "p": int(pid),
                "e": customer_email,
                "total": int(unit_amount),
            },
        ).fetchone()

        order_id = int(order_row[0])

        stripe.api_key = stripe_secret_key

        meta = {
            "tenant_id": str(tenant_id),
            "product_id": str(pid),
            "order_id": str(order_id),
        }

        product_data = {
            "name": title or f"Product {pid}",
        }
        if description:
            product_data["description"] = description
        # ✅ don't send images=None (omit field instead)
        if image_url:
            product_data["images"] = [image_url]

        session_kwargs = {
            "ui_mode": "embedded",
            "mode": "payment",
            "client_reference_id": str(order_id),
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "unit_amount": unit_amount,
                        "currency": currency,
                        "product_data": product_data,
                    },
                }
            ],
            "metadata": meta,
            "payment_intent_data": {"metadata": meta},
            "return_url": return_url,
        }

        if customer_email:
            session_kwargs["customer_email"] = customer_email

        session = stripe.checkout.Session.create(**session_kwargs)

        # 3) Persist stripe_session_id (scoped by tenant too)
        db.execute(
            text(
                """
                update orders
                   set stripe_session_id = :sid
                 where id = :oid and tenant_id = :t
                """
            ),
            {"sid": str(session["id"]), "oid": int(order_id), "t": int(tenant_id)},
        )

        db.commit()

        return {
            "ok": True,
            "tenant_id": tenant_id,
            "order_id": order_id,
            "id": session["id"],
            "client_secret": session["client_secret"],
            "return_url": return_url,
            "frontend_base": frontend_base,
        }

    except Exception as e:
        db.rollback()
        return {
            "ok": False,
            "message": f"{type(e).__name__}: {str(e)}",
            "tenant_id": tenant_id,
        }