# app/api/routes/orders.py
#
# Updated to return orders.total_cents ✅
from __future__ import annotations

from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, date
import re

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request

router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_MIN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}$")


def _try_parse_date_query(q: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """
    Accepts:
      - YYYY-MM-DD -> [start_of_day, next_day)
      - YYYY-MM-DD HH:MM -> [that_minute, +1min)
      - ISO-ish datetime -> [start_of_day, next_day) if parseable
    Returns (start, end) for a range filter on created_at.
    """
    s = (q or "").strip()
    if not s:
        return None, None

    if _DATE_RE.match(s):
        d = datetime.strptime(s, "%Y-%m-%d")
        return d, d + timedelta(days=1)

    if _DATETIME_MIN_RE.match(s):
        # normalize " " to "T" doesn’t matter, we parse with space
        s2 = s.replace("T", " ")
        dt = datetime.strptime(s2, "%Y-%m-%d %H:%M")
        return dt, dt + timedelta(minutes=1)

    # best-effort ISO parse (python 3.11 handles many forms)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        # match the day of that timestamp
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    except Exception:
        return None, None


@router.get("/orders/paged")
def list_orders_paged(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),

    # existing filters
    status: Optional[str] = Query(None, description="Filter by order status (pending/paid/fulfilled/expired)"),
    q: Optional[str] = Query(None, description="Search by buyer_email, status, stripe_session_id, order id, or date"),

    # ✅ explicit extra filters (optional but recommended)
    order_id: Optional[int] = Query(None, ge=1, description="Filter by exact order id"),
    date_from: Optional[datetime] = Query(None, description="Filter orders created at/after this datetime"),
    date_to: Optional[datetime] = Query(None, description="Filter orders created before this datetime"),

    include_product: bool = Query(True, description="If true, include basic product info"),
):
    offset = (page - 1) * page_size

    where_parts = ["o.tenant_id = :t"]
    params = {"t": int(tenant_id), "limit": int(page_size), "offset": int(offset)}

    # ----- explicit filters -----
    st_clean = (status or "").strip().lower()
    if st_clean:
        where_parts.append("o.status = :st")
        params["st"] = st_clean

    if order_id:
        where_parts.append("o.id = :oid")
        params["oid"] = int(order_id)

    if date_from:
        where_parts.append("o.created_at >= :date_from")
        params["date_from"] = date_from

    if date_to:
        where_parts.append("o.created_at < :date_to")
        params["date_to"] = date_to

    # ----- q search (email, stripe id, status, order id, date) -----
    q_clean = (q or "").strip()
    q_lower = q_clean.lower()

    if q_lower:
        # if q looks like an int -> allow direct id match
        q_as_int = None
        if q_lower.isdigit():
            try:
                q_as_int = int(q_lower)
            except Exception:
                q_as_int = None

        # if q looks like a date/datetime -> build a created_at range
        q_date_from, q_date_to = _try_parse_date_query(q_lower)

        # build OR conditions (param-safe)
        or_parts = [
            "lower(coalesce(o.buyer_email,'')) like :q_like",
            "lower(coalesce(o.stripe_session_id,'')) like :q_like",
            "lower(coalesce(o.status,'')) like :q_like",
        ]
        params["q_like"] = f"%{q_lower}%"

        if q_as_int is not None:
            or_parts.append("o.id = :q_id")
            params["q_id"] = q_as_int

        if q_date_from and q_date_to:
            or_parts.append("(o.created_at >= :q_date_from and o.created_at < :q_date_to)")
            params["q_date_from"] = q_date_from
            params["q_date_to"] = q_date_to

        where_parts.append("(" + " or ".join(or_parts) + ")")

    where_sql = " and ".join(where_parts)

    if include_product:
        rows = db.execute(
            text(f"""
                select
                    o.id,
                    o.tenant_id,
                    o.product_id,
                    o.buyer_email,
                    o.stripe_session_id,
                    o.status,
                    o.created_at,
                    o.total_cents,

                    p.slug as product_slug,
                    p.title as product_title,
                    p.image_url as product_image_url,
                    p.price as product_price,
                    p.discounted_price as product_discounted_price,
                    p.currency as product_currency,

                    count(*) over() as total_count
                  from orders o
                  left join products p
                    on p.id = o.product_id
                   and p.tenant_id = o.tenant_id
                 where {where_sql}
                 order by o.created_at desc, o.id desc
                 limit :limit offset :offset
            """),
            params,
        ).fetchall()
    else:
        rows = db.execute(
            text(f"""
                select
                    o.id,
                    o.tenant_id,
                    o.product_id,
                    o.buyer_email,
                    o.stripe_session_id,
                    o.status,
                    o.created_at,
                    o.total_cents,
                    count(*) over() as total_count
                  from orders o
                 where {where_sql}
                 order by o.created_at desc, o.id desc
                 limit :limit offset :offset
            """),
            params,
        ).fetchall()

    total = int(rows[0][-1]) if rows else 0
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    items: List[dict] = []
    if include_product:
        # 0..7 order fields, 8..13 product fields
        for r in rows or []:
            created_at = r[6].isoformat() if getattr(r[6], "isoformat", None) else str(r[6])
            items.append(
                {
                    "id": int(r[0]),
                    "tenant_id": int(r[1]) if r[1] is not None else None,
                    "product_id": int(r[2]) if r[2] is not None else None,
                    "buyer_email": r[3],
                    "stripe_session_id": r[4],
                    "status": r[5],
                    "created_at": created_at,
                    "total_cents": int(r[7]) if r[7] is not None else None,
                    "product": {
                        "slug": r[8],
                        "title": r[9],
                        "image_url": r[10],
                        "price": str(r[11]) if r[11] is not None else None,
                        "discounted_price": str(r[12]) if r[12] is not None else None,
                        "currency": r[13],
                    },
                }
            )
    else:
        for r in rows or []:
            created_at = r[6].isoformat() if getattr(r[6], "isoformat", None) else str(r[6])
            items.append(
                {
                    "id": int(r[0]),
                    "tenant_id": int(r[1]) if r[1] is not None else None,
                    "product_id": int(r[2]) if r[2] is not None else None,
                    "buyer_email": r[3],
                    "stripe_session_id": r[4],
                    "status": r[5],
                    "created_at": created_at,
                    "total_cents": int(r[7]) if r[7] is not None else None,
                }
            )

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "page": int(page),
        "page_size": int(page_size),
        "total": int(total),
        "total_pages": int(total_pages),
        "items": items,
    }


@router.get("/orders/{order_id}/enrollments")
def list_order_enrollments(
    order_id: int,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            select id, tenant_id, order_id, moodle_course_id, moodle_user_id, status, error, created_at
              from order_enrollments
             where tenant_id = :t
               and order_id = :oid
             order by created_at asc, id asc
        """),
        {"t": int(tenant_id), "oid": int(order_id)},
    ).fetchall()

    items = [
        {
            "id": int(r[0]),
            "tenant_id": int(r[1]),
            "order_id": int(r[2]),
            "moodle_course_id": int(r[3]) if r[3] is not None else None,
            "moodle_user_id": int(r[4]) if r[4] is not None else None,
            "status": r[5],
            "error": r[6],
            "created_at": r[7].isoformat() if getattr(r[7], "isoformat", None) else str(r[7]),
        }
        for r in (rows or [])
    ]

    return {"ok": True, "tenant_id": int(tenant_id), "order_id": int(order_id), "items": items}


@router.get("/orders/{order_id}")
def get_order_detail(
    order_id: int,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
    include_product: bool = Query(True),
    include_enrollments: bool = Query(True),
    include_product_courses: bool = Query(True),
    include_product_categories: bool = Query(True),
):
    # -----------------------------
    # 1) Base order (+ product)
    # -----------------------------
    if include_product:
        row = db.execute(
            text("""
                select
                    o.id,
                    o.tenant_id,
                    o.product_id,
                    o.buyer_email,
                    o.stripe_session_id,
                    o.status,
                    o.created_at,
                    o.total_cents,

                    p.slug as product_slug,
                    p.title as product_title,
                    p.image_url as product_image_url,
                    p.price as product_price,
                    p.discounted_price as product_discounted_price,
                    p.currency as product_currency
                from orders o
                left join products p
                  on p.id = o.product_id
                 and p.tenant_id = o.tenant_id
               where o.tenant_id = :t
                 and o.id = :oid
               limit 1
            """),
            {"t": int(tenant_id), "oid": int(order_id)},
        ).fetchone()
    else:
        row = db.execute(
            text("""
                select
                    o.id,
                    o.tenant_id,
                    o.product_id,
                    o.buyer_email,
                    o.stripe_session_id,
                    o.status,
                    o.created_at,
                    o.total_cents
                from orders o
               where o.tenant_id = :t
                 and o.id = :oid
               limit 1
            """),
            {"t": int(tenant_id), "oid": int(order_id)},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Order not found")

    created_at = row[6].isoformat() if getattr(row[6], "isoformat", None) else str(row[6])

    order: Dict[str, Any] = {
        "id": int(row[0]),
        "tenant_id": int(row[1]) if row[1] is not None else None,
        "product_id": int(row[2]) if row[2] is not None else None,
        "buyer_email": row[3],
        "stripe_session_id": row[4],
        "status": row[5],
        "created_at": created_at,
        "total_cents": int(row[7]) if row[7] is not None else None,
    }

    product_id = order["product_id"]

    if include_product:
        order["product"] = {
            "slug": row[8],
            "title": row[9],
            "image_url": row[10],
            "price": str(row[11]) if row[11] is not None else None,
            "discounted_price": str(row[12]) if row[12] is not None else None,
            "currency": row[13],
        }

    # -----------------------------
    # 2) Product categories + courses (via product_id)
    # -----------------------------
    if product_id and include_product_categories:
        cat_rows = db.execute(
            text("""
                select c.id, c.name, c.slug, c.moodle_category_id
                  from product_categories pc
                  join categories c
                    on c.id = pc.category_id
                   and c.tenant_id = pc.tenant_id
                 where pc.tenant_id = :t
                   and pc.product_id = :pid
                 order by c.name asc
            """),
            {"t": int(tenant_id), "pid": int(product_id)},
        ).fetchall()

        order["product_categories"] = [
            {
                "id": int(r[0]),
                "name": r[1],
                "slug": r[2],
                "moodle_category_id": int(r[3]) if r[3] is not None else None,
            }
            for r in (cat_rows or [])
        ]

    if product_id and include_product_courses:
        course_rows = db.execute(
            text("""
                select c.id, c.moodle_course_id, c.fullname, c.summary
                  from product_courses pc
                  join courses c
                    on c.id = pc.course_id
                   and c.tenant_id = pc.tenant_id
                 where pc.tenant_id = :t
                   and pc.product_id = :pid
                 order by c.fullname asc
            """),
            {"t": int(tenant_id), "pid": int(product_id)},
        ).fetchall()

        order["product_courses"] = [
            {
                "id": int(r[0]),
                "moodle_course_id": int(r[1]) if r[1] is not None else None,
                "fullname": r[2],
                "summary": r[3],
            }
            for r in (course_rows or [])
        ]

    # -----------------------------
    # 3) Enrollments for this order
    # -----------------------------
    if include_enrollments:
        enr_rows = db.execute(
            text("""
                select id, tenant_id, order_id, moodle_course_id, moodle_user_id, status, error, created_at
                  from order_enrollments
                 where tenant_id = :t
                   and order_id = :oid
                 order by created_at asc, id asc
            """),
            {"t": int(tenant_id), "oid": int(order_id)},
        ).fetchall()

        order["enrollments"] = [
            {
                "id": int(r[0]),
                "tenant_id": int(r[1]) if r[1] is not None else None,
                "order_id": int(r[2]) if r[2] is not None else None,
                "moodle_course_id": int(r[3]) if r[3] is not None else None,
                "moodle_user_id": int(r[4]) if r[4] is not None else None,
                "status": r[5],
                "error": r[6],
                "created_at": r[7].isoformat() if getattr(r[7], "isoformat", None) else str(r[7]),
            }
            for r in (enr_rows or [])
        ]

    return {"ok": True, "tenant_id": int(tenant_id), "order": order}