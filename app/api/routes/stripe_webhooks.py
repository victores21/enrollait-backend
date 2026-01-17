# from fastapi import APIRouter, Depends, Request
# from sqlalchemy.orm import Session
# from sqlalchemy import text
# import stripe

# from app.core.db import get_db

# router = APIRouter()


# def _get_tenant_stripe(db: Session, tenant_id: int) -> tuple[str | None, str | None]:
#     row = db.execute(
#         text("""
#             select stripe_secret_key, stripe_webhook_secret
#               from tenants
#              where id = :id
#              limit 1
#         """),
#         {"id": tenant_id},
#     ).fetchone()

#     if not row:
#         return (None, None)

#     return (row[0], row[1])


# def _mark_order_paid(db: Session, tenant_id: int, stripe_session_id: str, buyer_email: str | None):
#     """
#     MVP:
#     - Find order by (tenant_id, stripe_session_id)
#     - Set status='paid' if not already
#     """
#     row = db.execute(
#         text("""
#             update orders
#                set status = 'paid',
#                    buyer_email = coalesce(buyer_email, :email)
#              where tenant_id = :t
#                and stripe_session_id = :sid
#                and status <> 'paid'
#             returning id, product_id
#         """),
#         {"t": tenant_id, "sid": stripe_session_id, "email": buyer_email},
#     ).fetchone()

#     db.commit()
#     return row  # None if not found or already paid


# def _mark_order_expired(db: Session, tenant_id: int, stripe_session_id: str):
#     db.execute(
#         text("""
#             update orders
#                set status = 'expired'
#              where tenant_id = :t
#                and stripe_session_id = :sid
#                and status not in ('paid', 'expired')
#         """),
#         {"t": tenant_id, "sid": stripe_session_id},
#     )
#     db.commit()


# @router.post("/webhooks/stripe/{tenant_id}")
# async def stripe_webhook(tenant_id: int, request: Request, db: Session = Depends(get_db)):
#     # 1) Load tenant webhook secret
#     stripe_secret_key, webhook_secret = _get_tenant_stripe(db, tenant_id)
#     if not webhook_secret:
#         # Return 200 so Stripe doesn't keep retrying forever, but log this on your side.
#         return {"ok": False, "message": "Tenant Stripe webhook not configured"}

#     # 2) Read raw body + signature header (must be raw for verification)
#     payload = await request.body()
#     sig_header = request.headers.get("stripe-signature")
#     if not sig_header:
#         return {"ok": False, "message": "Missing Stripe-Signature header"}

#     # 3) Verify event signature
#     try:
#         event = stripe.Webhook.construct_event(
#             payload=payload,
#             sig_header=sig_header,
#             secret=webhook_secret,
#         )
#     except stripe.error.SignatureVerificationError:
#         return {"ok": False, "message": "Invalid Stripe signature"}
#     except Exception as e:
#         return {"ok": False, "message": f"Webhook error: {type(e).__name__}: {str(e)}"}

#     event_type = event.get("type")
#     obj = (event.get("data") or {}).get("object") or {}

#     # 4) Handle events you care about
#     if event_type == "checkout.session.completed":
#         session_id = obj.get("id")
#         buyer_email = None

#         # Stripe can store email in a few places depending on checkout settings
#         customer_details = obj.get("customer_details") or {}
#         buyer_email = customer_details.get("email") or obj.get("customer_email")

#         if not session_id:
#             return {"ok": True}

#         # Optional: set api key if later you want to fetch more data from Stripe
#         if stripe_secret_key:
#             stripe.api_key = stripe_secret_key

#         updated = _mark_order_paid(db, tenant_id, session_id, buyer_email)

#         print("Fired", "checkout.session.completed", "for tenant", tenant_id)
#         print("Payload", payload)
#         # If you want, trigger enrollment here (recommended after marking paid).
#         # Example (pseudo):
#         # if updated:
#         #   order_id = updated[0]
#         #   product_id = updated[1]
#         #   enqueue_enrollment_job(tenant_id, order_id, product_id, buyer_email)

#         return {"ok": True}

#     if event_type == "checkout.session.expired":
#         session_id = obj.get("id")
#         if session_id:
#             _mark_order_expired(db, tenant_id, session_id)
#         return {"ok": True}

#     # Ignore other events for now (Stripe expects 2xx)
#     return {"ok": True}



from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
import stripe

from app.core.db import get_db

router = APIRouter()


def _get_tenant_stripe(db: Session, tenant_id: int) -> tuple[str | None, str | None]:
    row = db.execute(
        text("""
            select stripe_secret_key, stripe_webhook_secret
              from tenants
             where id = :id
             limit 1
        """),
        {"id": tenant_id},
    ).fetchone()

    if not row:
        return (None, None)

    return (row[0], row[1])


def _mark_order_paid(db: Session, tenant_id: int, stripe_session_id: str, buyer_email: str | None):
    """
    MVP:
    - Find order by (tenant_id, stripe_session_id)
    - Set status='paid' if not already
    """
    row = db.execute(
        text("""
            update orders
               set status = 'paid',
                   buyer_email = coalesce(buyer_email, :email)
             where tenant_id = :t
               and stripe_session_id = :sid
               and status <> 'paid'
            returning id, product_id
        """),
        {"t": tenant_id, "sid": stripe_session_id, "email": buyer_email},
    ).fetchone()

    db.commit()
    return row  # None if not found or already paid


def _mark_order_expired(db: Session, tenant_id: int, stripe_session_id: str):
    db.execute(
        text("""
            update orders
               set status = 'expired'
             where tenant_id = :t
               and stripe_session_id = :sid
               and status not in ('paid', 'expired')
        """),
        {"t": tenant_id, "sid": stripe_session_id},
    )
    db.commit()


def _extract_tenant_id_from_event(obj: dict) -> int | None:
    """
    Best-effort tenant extraction (no extra Stripe API calls):
    1) Checkout Session metadata.tenant_id
    2) Checkout Session client_reference_id formatted like "tenant:product" or "tenant|product"
    """
    md = obj.get("metadata") or {}
    tenant_val = md.get("tenant_id")
    if tenant_val:
        try:
            return int(str(tenant_val))
        except Exception:
            return None

    cref = obj.get("client_reference_id")
    if cref:
        # accept formats: "12:99" or "12|99" or just "12"
        for sep in (":", "|", "_"):
            if sep in str(cref):
                first = str(cref).split(sep, 1)[0]
                try:
                    return int(first)
                except Exception:
                    return None
        try:
            return int(str(cref))
        except Exception:
            return None

    return None


@router.post("/webhooks/stripe/{tenant_id}")
async def stripe_webhook(tenant_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Tenant-specific Stripe webhook endpoint.

    Improvements:
    - Verify the tenant from the event (metadata/client_reference_id) matches the URL tenant_id.
    - Optionally use the event tenant for DB updates (safer if endpoint misconfigured).
    """
    # 1) Load tenant webhook secret by URL tenant_id (needed to verify signature)
    stripe_secret_key, webhook_secret = _get_tenant_stripe(db, tenant_id)
    if not webhook_secret:
        # Return 200 so Stripe doesn't keep retrying forever, but log this on your side.
        return {"ok": False, "message": "Tenant Stripe webhook not configured"}

    # 2) Read raw body + signature header (must be raw for verification)
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        return {"ok": False, "message": "Missing Stripe-Signature header"}

    # 3) Verify event signature
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=webhook_secret,
        )
    except stripe.error.SignatureVerificationError:
        return {"ok": False, "message": "Invalid Stripe signature"}
    except Exception as e:
        return {"ok": False, "message": f"Webhook error: {type(e).__name__}: {str(e)}"}

    event_type = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}

    # 4) Tenant verification (do NOT trust URL alone)
    tenant_id_from_event = _extract_tenant_id_from_event(obj)

    # If event includes tenant_id, enforce it matches URL tenant_id
    # (If it's missing, we allow it, but you should ensure your Checkout session sets it.)
    if tenant_id_from_event is not None and int(tenant_id_from_event) != int(tenant_id):
        # Return 200 to avoid endless retries; but do NOT process.
        return {
            "ok": True,
            "ignored": True,
            "message": "Tenant mismatch (event tenant != URL tenant)",
            "url_tenant_id": tenant_id,
            "event_tenant_id": tenant_id_from_event,
        }

    # Use event tenant when available (safer), else fall back to URL tenant
    effective_tenant_id = tenant_id_from_event if tenant_id_from_event is not None else tenant_id

    # Optional: set api key if later you want to fetch more data from Stripe
    if stripe_secret_key:
        stripe.api_key = stripe_secret_key

    # 5) Handle events you care about
    if event_type == "checkout.session.completed":
        session_id = obj.get("id")
        if not session_id:
            return {"ok": True}

        # Stripe can store email in a few places depending on checkout settings
        customer_details = obj.get("customer_details") or {}
        buyer_email = customer_details.get("email") or obj.get("customer_email")

        updated = _mark_order_paid(db, effective_tenant_id, session_id, buyer_email)

        print("Fired", "checkout.session.completed", "tenant_url", tenant_id, "tenant_effective", effective_tenant_id)

        # If you want, trigger enrollment here (recommended after marking paid).
        # if updated:
        #   order_id = updated[0]
        #   product_id = updated[1]
        #   enqueue_enrollment_job(effective_tenant_id, order_id, product_id, buyer_email)

        return {"ok": True}

    if event_type == "checkout.session.expired":
        session_id = obj.get("id")
        if session_id:
            _mark_order_expired(db, effective_tenant_id, session_id)
        return {"ok": True}

    # Ignore other events for now (Stripe expects 2xx)
    return {"ok": True}
