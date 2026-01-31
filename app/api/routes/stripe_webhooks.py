
# app/api/routes/stripe_webhook.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
import stripe

import json
import re
import secrets
import string
from datetime import datetime, timezone

from app.core.db import get_db
from app.services.moodle import MoodleClient, MoodleError

router = APIRouter()

# -----------------------------------------------------------------------------
# Module-level "run once" flags to avoid DDL on every webhook hit
# -----------------------------------------------------------------------------
_USER_MAP_READY = False
_ORDER_ENROLLMENTS_UNIQ_READY = False
_WEBHOOK_HEALTH_READY = False  # ✅ NEW

# -----------------------------
# Small logging helper
# -----------------------------
def _log(*args):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[stripe_webhook] {ts}", *args)


# -----------------------------
# Stripe helpers
# -----------------------------
def _get_tenant_stripe(db: Session, tenant_id: int) -> tuple[str | None, str | None]:
    row = db.execute(
        text(
            """
            select stripe_secret_key, stripe_webhook_secret
              from tenants
             where id = :id
             limit 1
            """
        ),
        {"id": int(tenant_id)},
    ).fetchone()

    if not row:
        return (None, None)

    return (row[0], row[1])


def _extract_order_id_from_event(obj: dict) -> int | None:
    """
    Strong binding: order_id must come from metadata.order_id or client_reference_id.
    """
    md = obj.get("metadata") or {}
    oid = md.get("order_id")
    if oid:
        try:
            return int(str(oid))
        except Exception:
            return None

    cref = obj.get("client_reference_id")
    if cref:
        try:
            return int(str(cref))
        except Exception:
            return None

    return None


def _extract_total_cents_from_session(obj: dict) -> int | None:
    """
    Stripe Checkout Session:
      - amount_total is integer in smallest currency unit (cents for USD)
    """
    amt = obj.get("amount_total")
    if amt is None:
        return None
    try:
        amt_int = int(amt)
        if amt_int < 0:
            return None
        return amt_int
    except Exception:
        return None

def _ensure_webhook_health_table(db: Session) -> None:
    """
    Stores the last time we successfully verified a Stripe webhook signature per tenant.
    No secrets stored.
    """
    global _WEBHOOK_HEALTH_READY
    if _WEBHOOK_HEALTH_READY:
        return

    try:
        db.execute(
            text(
                """
                create table if not exists stripe_webhook_health (
                  tenant_id bigint primary key references tenants(id) on delete cascade,
                  last_verified_at timestamptz not null default now(),
                  last_event_type text null,
                  last_event_id text null,
                  last_session_id text null
                );
                """
            )
        )
        db.commit()
        _WEBHOOK_HEALTH_READY = True
    except Exception as e:
        db.rollback()
        _log("warn: could not ensure stripe_webhook_health table:", type(e).__name__, str(e))


def _upsert_webhook_health(
    db: Session,
    tenant_id: int,
    event_type: str | None,
    event_id: str | None,
    session_id: str | None,
) -> None:
    _ensure_webhook_health_table(db)
    db.execute(
        text(
            """
            insert into stripe_webhook_health
                (tenant_id, last_verified_at, last_event_type, last_event_id, last_session_id)
            values
                (:t, now(), :et, :eid, :sid)
            on conflict (tenant_id)
            do update set
                last_verified_at = excluded.last_verified_at,
                last_event_type = excluded.last_event_type,
                last_event_id = excluded.last_event_id,
                last_session_id = excluded.last_session_id
            """
        ),
        {
            "t": int(tenant_id),
            "et": str(event_type) if event_type else None,
            "eid": str(event_id) if event_id else None,
            "sid": str(session_id) if session_id else None,
        },
    )
    # commit is handled by caller


# -----------------------------
# Orders (STRICT)
# -----------------------------
def _get_order_by_id(db: Session, order_id: int):
    """
    Fetch order row (no locks). Used only for the initial tenant lookup
    BEFORE signature verification.
    """
    return db.execute(
        text(
            """
            select id, tenant_id, product_id, buyer_email, stripe_session_id, status, total_cents
              from orders
             where id = :oid
             limit 1
            """
        ),
        {"oid": int(order_id)},
    ).fetchone()


def _get_order_by_id_for_update(db: Session, order_id: int):
    """
    Lock the order row to prevent double-fulfillment under concurrent webhook delivery.
    Only call this AFTER signature verification.
    """
    return db.execute(
        text(
            """
            select id, tenant_id, product_id, buyer_email, stripe_session_id, status, total_cents
              from orders
             where id = :oid
             limit 1
             for update
            """
        ),
        {"oid": int(order_id)},
    ).fetchone()


def _mark_paid_and_save_total(
    db: Session,
    order_id: int,
    buyer_email: str | None,
    total_cents: int | None,
) -> None:
    """
    One UPDATE to:
      - set status to 'paid' unless already 'fulfilled'
      - fill buyer_email if empty
      - save total_cents if provided and changed
    """
    db.execute(
        text(
            """
            update orders
               set status = case
                   when status = 'fulfilled' then status
                   else 'paid'
               end,
               buyer_email = case
                   when (buyer_email is null or buyer_email = '')
                        and :email is not null then :email
                   else buyer_email
               end,
               total_cents = case
                   when :tc is null then total_cents
                   when total_cents is null or total_cents <> :tc then :tc
                   else total_cents
               end
             where id = :oid
            """
        ),
        {
            "oid": int(order_id),
            "email": buyer_email,
            "tc": int(total_cents) if total_cents is not None else None,
        },
    )


def _set_order_status(db: Session, order_id: int, status: str) -> None:
    db.execute(
        text(
            """
            update orders
               set status = :st
             where id = :oid
            """
        ),
        {"oid": int(order_id), "st": str(status)},
    )


def _mark_order_expired(db: Session, tenant_id: int, stripe_session_id: str) -> None:
    """
    Keep same behavior as before: write + commit in this helper.
    """
    try:
        db.execute(
            text(
                """
                update orders
                   set status = 'expired'
                 where tenant_id = :t
                   and stripe_session_id = :sid
                   and status not in ('paid', 'expired', 'fulfilled')
                """
            ),
            {"t": int(tenant_id), "sid": str(stripe_session_id)},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


# -----------------------------
# Order enrollment logging (UPSERT)
# -----------------------------
def _ensure_order_enrollments_unique(db: Session) -> None:
    """
    ⚠️ DDL in webhooks is not ideal, but kept for compatibility.
    Optimized: run only once per process.
    """
    global _ORDER_ENROLLMENTS_UNIQ_READY
    if _ORDER_ENROLLMENTS_UNIQ_READY:
        return

    try:
        db.execute(
            text(
                """
                do $$
                begin
                  if not exists (
                    select 1
                      from pg_constraint
                     where conname = 'order_enrollments_order_id_moodle_course_id_key'
                  ) then
                    alter table order_enrollments
                      add constraint order_enrollments_order_id_moodle_course_id_key
                      unique (order_id, moodle_course_id);
                  end if;
                end $$;
                """
            )
        )
        db.commit()
        _ORDER_ENROLLMENTS_UNIQ_READY = True
    except Exception:
        db.rollback()
        _log("warn: could not ensure unique constraint on order_enrollments(order_id,moodle_course_id)")


def _upsert_order_enrollment(
    db: Session,
    tenant_id: int,
    order_id: int,
    moodle_course_id: int,
    status: str,
    moodle_user_id: int | None = None,
    error: str | None = None,
) -> int | None:
    """
    Optimized: NO COMMIT inside.
    Caller controls commit frequency (we commit once per course attempt).
    """
    try:
        row = db.execute(
            text(
                """
                insert into order_enrollments
                    (tenant_id, order_id, moodle_course_id, moodle_user_id, status, error, created_at)
                values
                    (:t, :oid, :cid, :uid, :st, :err, now())
                on conflict (order_id, moodle_course_id)
                do update set
                    tenant_id = excluded.tenant_id,
                    moodle_user_id = coalesce(excluded.moodle_user_id, order_enrollments.moodle_user_id),
                    status = excluded.status,
                    error = excluded.error
                returning id
                """
            ),
            {
                "t": int(tenant_id),
                "oid": int(order_id),
                "cid": int(moodle_course_id),
                "uid": int(moodle_user_id) if moodle_user_id is not None else None,
                "st": str(status),
                "err": (str(error)[:2000] if error else None),
            },
        ).fetchone()
        return int(row[0]) if row else None
    except Exception as e:
        _log("warn: _upsert_order_enrollment failed:", type(e).__name__, str(e))
        return None


def _get_already_enrolled_courses(db: Session, order_id: int) -> set[int]:
    rows = db.execute(
        text(
            """
            select moodle_course_id
              from order_enrollments
             where order_id = :oid
               and status = 'enrolled'
            """
        ),
        {"oid": int(order_id)},
    ).fetchall()

    out: set[int] = set()
    for r in rows or []:
        if r and r[0] is not None:
            try:
                out.add(int(r[0]))
            except Exception:
                pass
    return out


# -----------------------------
# Moodle helpers
# -----------------------------
def _get_tenant_moodle(db: Session, tenant_id: int) -> tuple[str | None, str | None]:
    row = db.execute(
        text(
            """
            select moodle_url, moodle_token
              from tenants
             where id = :id
             limit 1
            """
        ),
        {"id": int(tenant_id)},
    ).fetchone()

    if not row or not row[0] or not row[1]:
        return (None, None)

    return (str(row[0]).rstrip("/"), str(row[1]).strip())


def _ensure_user_map_table(db: Session) -> None:
    """
    ⚠️ DDL in webhooks is not ideal, but kept for compatibility.
    Optimized: run only once per process.
    """
    global _USER_MAP_READY
    if _USER_MAP_READY:
        return

    db.execute(
        text(
            """
            create table if not exists user_map (
              id bigserial primary key,
              tenant_id bigint not null references tenants(id) on delete cascade,
              email text not null,
              moodle_user_id bigint not null,
              created_at timestamptz not null default now(),
              unique (tenant_id, email)
            );
            """
        )
    )
    db.commit()
    _USER_MAP_READY = True


def _upsert_user_map(db: Session, tenant_id: int, email: str, moodle_user_id: int) -> None:
    _ensure_user_map_table(db)
    db.execute(
        text(
            """
            insert into user_map (tenant_id, email, moodle_user_id)
            values (:t, :e, :uid)
            on conflict (tenant_id, email)
            do update set moodle_user_id = excluded.moodle_user_id;
            """
        ),
        {"t": int(tenant_id), "e": str(email), "uid": int(moodle_user_id)},
    )
    # no commit here; caller decides


def _gen_username(email: str) -> str:
    base = email.split("@")[0].lower()
    base = re.sub(r"[^a-z0-9._-]+", "", base)
    base = base[:18] if base else "user"
    suffix = secrets.token_hex(3)
    return f"{base}_{suffix}"


def _gen_temp_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%*_-"
    return "".join(secrets.choice(alphabet) for _ in range(16))


def _split_name(fullname: str | None) -> tuple[str, str]:
    if not fullname:
        return ("Student", "User")
    parts = [p for p in str(fullname).strip().split(" ") if p]
    if not parts:
        return ("Student", "User")
    if len(parts) == 1:
        return (parts[0][:100], "User")
    return (parts[0][:100], " ".join(parts[1:])[:100])


async def _find_moodle_user_id(moodle: MoodleClient, email: str) -> int | None:
    data = await moodle.call(
        "core_user_get_users",
        **{
            "criteria[0][key]": "email",
            "criteria[0][value]": email,
        },
    )
    users = data.get("users", []) if isinstance(data, dict) else []
    if not users:
        return None
    return int(users[0]["id"])


async def _create_moodle_user(moodle: MoodleClient, email: str, firstname: str, lastname: str) -> int:
    username = _gen_username(email)
    temp_password = _gen_temp_password()

    created = await moodle.call(
        "core_user_create_users",
        **{
            "users[0][username]": username,
            "users[0][password]": temp_password,
            "users[0][firstname]": firstname,
            "users[0][lastname]": lastname,
            "users[0][email]": email,
        },
    )
    return int(created[0]["id"])


def _get_product_course_ids_only_product_courses(db: Session, tenant_id: int, product_id: int) -> list[int]:
    rows = db.execute(
        text(
            """
            select c.moodle_course_id
              from product_courses pc
              join courses c
                on c.id = pc.course_id
               and c.tenant_id = pc.tenant_id
             where pc.tenant_id = :t
               and pc.product_id = :p
               and pc.course_id is not null
               and c.moodle_course_id is not null
             order by c.moodle_course_id asc
            """
        ),
        {"t": int(tenant_id), "p": int(product_id)},
    ).fetchall()

    ids: list[int] = []
    for r in rows or []:
        if not r or r[0] is None:
            continue
        try:
            ids.append(int(r[0]))
        except Exception:
            continue

    # unique + stable order
    return sorted(list(dict.fromkeys(ids)))


async def _enroll_user_in_course(moodle: MoodleClient, moodle_user_id: int, course_id: int, role_id: int = 5) -> None:
    await moodle.call(
        "enrol_manual_enrol_users",
        **{
            "enrolments[0][roleid]": int(role_id),
            "enrolments[0][userid]": int(moodle_user_id),
            "enrolments[0][courseid]": int(course_id),
        },
    )


async def _ensure_user_and_enroll(
    db: Session,
    tenant_id: int,
    buyer_email: str,
    buyer_name: str | None,
    product_id: int,
    order_id: int,
) -> dict:
    moodle_url, moodle_token = _get_tenant_moodle(db, tenant_id)
    if not moodle_url or not moodle_token:
        return {"ok": False, "message": "Tenant Moodle not configured", "tenant_id": tenant_id}

    moodle = MoodleClient(moodle_url, moodle_token)

    email = buyer_email.strip().lower()
    firstname, lastname = _split_name(buyer_name)

    try:
        moodle_user_id = await _find_moodle_user_id(moodle, email)
        _log("moodle find user", email, "=>", moodle_user_id)
    except Exception as e:
        return {"ok": False, "message": f"Find user failed: {type(e).__name__}: {str(e)}"}

    created = False
    if not moodle_user_id:
        try:
            moodle_user_id = await _create_moodle_user(moodle, email, firstname, lastname)
            created = True
            _log("moodle created user", email, "=>", moodle_user_id)
        except Exception as e:
            return {"ok": False, "message": f"Create user failed: {type(e).__name__}: {str(e)}"}

    # Ensure unique constraint once per process (kept for compatibility)
    _ensure_order_enrollments_unique(db)

    # Upsert user_map (no internal commit; we commit right after)
    try:
        _upsert_user_map(db, tenant_id, email, int(moodle_user_id))
        db.commit()
    except Exception as e:
        db.rollback()
        _log("warn: user_map upsert failed:", type(e).__name__, str(e))

    course_ids = _get_product_course_ids_only_product_courses(db, tenant_id, product_id)
    _log("courses for product", product_id, "=>", course_ids)

    if not course_ids:
        return {
            "ok": False,
            "message": "No Moodle courses linked to product in product_courses",
            "tenant_id": tenant_id,
            "product_id": product_id,
            "order_id": order_id,
            "moodle_user_id": int(moodle_user_id),
            "created_user": created,
        }

    already_enrolled = _get_already_enrolled_courses(db, order_id)
    _log("already enrolled for order", order_id, "=>", sorted(list(already_enrolled)))

    enrolled: list[int] = []
    skipped: list[int] = []

    for cid in course_ids:
        if int(cid) in already_enrolled:
            skipped.append(int(cid))
            continue

        # Log attempt (no commit yet)
        _upsert_order_enrollment(
            db=db,
            tenant_id=tenant_id,
            order_id=order_id,
            moodle_course_id=int(cid),
            moodle_user_id=int(moodle_user_id),
            status="attempt",
            error=None,
        )

        try:
            await _enroll_user_in_course(moodle, int(moodle_user_id), int(cid), role_id=5)

            _upsert_order_enrollment(
                db=db,
                tenant_id=tenant_id,
                order_id=order_id,
                moodle_course_id=int(cid),
                moodle_user_id=int(moodle_user_id),
                status="enrolled",
                error=None,
            )

            db.commit()  # ✅ one commit per course attempt (success path)
            enrolled.append(int(cid))
            _log("enrolled", email, "user_id", moodle_user_id, "course", cid, "order", order_id)

        except MoodleError as e:
            err = f"MoodleError: {str(e)}"
            _log("enroll failed course=", cid, "order=", order_id, err)

            _upsert_order_enrollment(
                db=db,
                tenant_id=tenant_id,
                order_id=order_id,
                moodle_course_id=int(cid),
                moodle_user_id=int(moodle_user_id),
                status="failed",
                error=err,
            )

            db.commit()  # ✅ one commit per course attempt (failure path)

            return {
                "ok": False,
                "message": err,
                "tenant_id": tenant_id,
                "product_id": product_id,
                "order_id": order_id,
                "moodle_user_id": int(moodle_user_id),
                "created_user": created,
                "enrolled_courses": enrolled,
                "skipped_courses": skipped,
            }

        except Exception as e:
            err = f"{type(e).__name__}: {str(e)}"
            _log("enroll failed course=", cid, "order=", order_id, err)

            _upsert_order_enrollment(
                db=db,
                tenant_id=tenant_id,
                order_id=order_id,
                moodle_course_id=int(cid),
                moodle_user_id=int(moodle_user_id),
                status="failed",
                error=err,
            )

            db.commit()  # ✅ one commit per course attempt (failure path)

            return {
                "ok": False,
                "message": err,
                "tenant_id": tenant_id,
                "product_id": product_id,
                "order_id": order_id,
                "moodle_user_id": int(moodle_user_id),
                "created_user": created,
                "enrolled_courses": enrolled,
                "skipped_courses": skipped,
            }

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "product_id": product_id,
        "order_id": order_id,
        "email": email,
        "moodle_user_id": int(moodle_user_id),
        "created_user": created,
        "enrolled_courses": enrolled,
        "skipped_courses": skipped,
    }


# -----------------------------
# Webhook (single endpoint version)
# -----------------------------
@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        _log("missing stripe-signature header")
        return {"ok": False, "message": "Missing Stripe-Signature header"}

    # 1) Parse JSON UNVERIFIED to extract order_id (we will use DB tenant to choose secret)
    try:
        event_json = json.loads(payload)
        obj_guess = ((event_json.get("data") or {}).get("object")) or {}
        order_id_guess = _extract_order_id_from_event(obj_guess)
    except Exception as e:
        _log("failed to parse json before verify:", type(e).__name__, str(e))
        return {"ok": False, "message": "Invalid JSON payload"}

    if not order_id_guess:
        _log("missing order_id in event; ignoring")
        return {"ok": True, "ignored": True, "message": "Missing order_id in Stripe event"}

    # 2) Load order to find tenant_id (source of truth)
    order_row_guess = _get_order_by_id(db, int(order_id_guess))
    if not order_row_guess:
        _log("order not found for order_id; ignoring", order_id_guess)
        return {"ok": True, "ignored": True, "message": "Order not found"}

    oid, tenant_id_db, product_id_db, buyer_email_db, stripe_session_id_db, status_db, existing_total_cents = order_row_guess
    tenant_id_db = int(tenant_id_db)

    # 3) Load correct webhook secret using DB tenant_id
    stripe_secret_key, webhook_secret = _get_tenant_stripe(db, tenant_id_db)
    if not webhook_secret:
        _log("tenant has no webhook secret configured:", tenant_id_db)
        return {
            "ok": True,
            "ignored": True,
            "message": "Tenant Stripe webhook not configured",
            "tenant_id": tenant_id_db,
        }

    # 4) Verify signature with tenant's webhook secret
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=webhook_secret,
        )
        # ✅ mark "webhook verified" for this tenant (signature passed)
        try:
            _upsert_webhook_health(
                db=db,
                tenant_id=int(tenant_id_db),
                event_type=str(event_type) if event_type else None,
                event_id=str(event_id) if event_id else None,
                session_id=str(session_id) if session_id else None,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            _log("warn: failed to update stripe_webhook_health:", type(e).__name__, str(e))        
    except stripe.error.SignatureVerificationError:
        _log("invalid stripe signature for tenant", tenant_id_db)
        return {"ok": False, "message": "Invalid Stripe signature"}
    except Exception as e:
        _log("webhook construct error:", type(e).__name__, str(e))
        return {"ok": False, "message": f"Webhook error: {type(e).__name__}: {str(e)}"}

    event_type = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}
    md = obj.get("metadata") or {}
    session_id = obj.get("id")

    _log("event_type:", event_type, "session_id:", session_id, "metadata:", md, "tenant:", tenant_id_db)

    # NOTE: do NOT set stripe.api_key here (global mutable, and not needed for signature verification)

    # -------------------------
    # checkout.session.completed
    # -------------------------
    if event_type == "checkout.session.completed":
        if not session_id:
            return {"ok": True}

        # ✅ MUST match order_id again using VERIFIED event
        order_id = _extract_order_id_from_event(obj)
        if not order_id or int(order_id) != int(oid):
            _log("order_id mismatch; ignoring", "db_oid", oid, "event_oid", order_id)
            return {"ok": True, "ignored": True, "message": "Order mismatch"}

        # ✅ session match (if stored)
        if stripe_session_id_db and str(stripe_session_id_db) != str(session_id):
            _log("session mismatch; ignoring", "order", oid, "db_sid", stripe_session_id_db, "event_sid", session_id)
            return {"ok": True, "ignored": True, "message": "Session mismatch"}

        # ✅ ensure paid
        payment_status = (obj.get("payment_status") or "").lower()
        if payment_status and payment_status != "paid":
            _log("not paid yet; ignoring", "order", oid, "payment_status", payment_status)
            return {"ok": True, "ignored": True, "message": "Payment not paid", "payment_status": payment_status}

        # ✅ buyer info
        customer_details = obj.get("customer_details") or {}
        stripe_email = (customer_details.get("email") or obj.get("customer_email") or "").strip().lower() or None
        buyer_name = customer_details.get("name")

        final_email = stripe_email or ((str(buyer_email_db).strip().lower()) if buyer_email_db else None)
        if not final_email:
            _log("missing buyer email; cannot fulfill", "order", oid, "session", session_id)
            return {"ok": True, "message": "Missing buyer email; cannot enroll", "order_id": int(oid)}

        if product_id_db is None:
            _log("order missing product_id; cannot fulfill", "order", oid)
            return {"ok": True, "message": "Order missing product_id; cannot enroll", "order_id": int(oid)}

        total_cents = _extract_total_cents_from_session(obj)

        # ✅ Concurrency safety: lock the order row AFTER verifying signature
        try:
            locked = _get_order_by_id_for_update(db, int(oid))
            if not locked:
                return {"ok": True, "ignored": True, "message": "Order not found after verify"}
            oid_l, tenant_l, product_l, buyer_email_l, sid_l, status_l, total_l = locked

            # replay safe: if already fulfilled, no-op
            if str(status_l) == "fulfilled":
                _log("already fulfilled; no-op", "order", oid)
                return {"ok": True, "message": "Already fulfilled", "order_id": int(oid)}

            # ✅ single update for paid + total (commit once)
            _mark_paid_and_save_total(db, int(oid), final_email, total_cents)
            db.commit()
        except Exception as e:
            db.rollback()
            _log("failed order lock/update", type(e).__name__, str(e))
            return {"ok": False, "message": f"Failed updating order: {type(e).__name__}: {str(e)}"}

        product_id = int(product_id_db)

        # ✅ fulfill
        result = await _ensure_user_and_enroll(
            db=db,
            tenant_id=int(tenant_id_db),
            buyer_email=str(final_email),
            buyer_name=buyer_name,
            product_id=int(product_id),
            order_id=int(oid),
        )

        # ✅ mark fulfilled on success
        if result.get("ok"):
            try:
                _set_order_status(db, int(oid), "fulfilled")
                db.commit()
            except Exception as e:
                db.rollback()
                _log("warn: failed to mark order fulfilled", "order", oid, type(e).__name__, str(e))

        return {
            "ok": True,
            "tenant_id": int(tenant_id_db),
            "order_id": int(oid),
            "total_cents": total_cents,
            "fulfillment": result,
        }

    # -------------------------
    # checkout.session.expired
    # -------------------------
    if event_type == "checkout.session.expired":
        if session_id:
            try:
                _mark_order_expired(db, tenant_id_db, str(session_id))
            except Exception as e:
                _log("mark expired failed:", type(e).__name__, str(e))
        return {"ok": True}

    return {"ok": True}