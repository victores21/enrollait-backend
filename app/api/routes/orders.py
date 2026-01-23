# # app/api/routes/orders.py

# from __future__ import annotations

# from typing import Optional, List
# from datetime import datetime

# from fastapi import APIRouter, Depends, Query
# from sqlalchemy.orm import Session
# from sqlalchemy import text

# from app.core.db import get_db
# from app.core.tenant import get_tenant_id_from_request  # ✅ infer tenant

# router = APIRouter()


# # -----------------------------
# # Optional: ensure table exists (safe)
# # -----------------------------
# def _ensure_orders_table(db: Session) -> None:
#     db.execute(text("create table if not exists orders (id bigserial primary key);"))
#     db.commit()

#     db.execute(text("alter table orders add column if not exists tenant_id bigint;"))
#     db.execute(text("alter table orders add column if not exists product_id bigint;"))
#     db.execute(text("alter table orders add column if not exists buyer_email text;"))
#     db.execute(text("alter table orders add column if not exists stripe_session_id text;"))
#     db.execute(text("alter table orders add column if not exists status text not null default 'pending';"))
#     db.execute(text("alter table orders add column if not exists created_at timestamptz not null default now();"))
#     db.commit()


# # -----------------------------
# # Endpoint: list orders (paged)
# # -----------------------------
# @router.get("/orders/paged")
# def list_orders_paged(
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     db: Session = Depends(get_db),
#     page: int = Query(1, ge=1),
#     page_size: int = Query(20, ge=1, le=200),
#     status: Optional[str] = Query(None, description="Filter by order status (pending/paid/fulfilled/expired)"),
#     q: Optional[str] = Query(None, description="Search by buyer_email or stripe_session_id"),
#     include_product: bool = Query(True, description="If true, include basic product info"),
# ):
#     """
#     Returns paged orders for the current tenant (tenant isolation).
#     Optional filters:
#       - status
#       - q (buyer_email / stripe_session_id)
#       - include_product (joins products)
#     """
#     _ensure_orders_table(db)

#     offset = (page - 1) * page_size

#     where_parts = ["o.tenant_id = :t"]
#     params = {"t": int(tenant_id), "limit": int(page_size), "offset": int(offset)}

#     # Filter by status
#     if status and status.strip():
#         where_parts.append("o.status = :st")
#         params["st"] = status.strip().lower()

#     # Search
#     q_clean = (q or "").strip().lower()
#     if q_clean:
#         params["q"] = f"%{q_clean}%"
#         where_parts.append(
#             "(lower(coalesce(o.buyer_email,'')) like :q or lower(coalesce(o.stripe_session_id,'')) like :q)"
#         )

#     where_sql = " and ".join(where_parts)

#     # Total count
#     total = db.execute(
#         text(f"select count(*)::int from orders o where {where_sql}"),
#         params,
#     ).scalar() or 0

#     # Rows
#     if include_product:
#         rows = db.execute(
#             text(
#                 f"""
#                 select
#                     o.id,
#                     o.tenant_id,
#                     o.product_id,
#                     o.buyer_email,
#                     o.stripe_session_id,
#                     o.status,
#                     o.created_at,

#                     p.slug as product_slug,
#                     p.title as product_title,
#                     p.image_url as product_image_url,
#                     p.price as product_price,
#                     p.discounted_price as product_discounted_price,
#                     p.currency as product_currency
#                   from orders o
#                   left join products p
#                     on p.id = o.product_id
#                    and p.tenant_id = o.tenant_id
#                  where {where_sql}
#                  order by o.created_at desc, o.id desc
#                  limit :limit offset :offset
#                 """
#             ),
#             params,
#         ).fetchall()
#     else:
#         rows = db.execute(
#             text(
#                 f"""
#                 select
#                     o.id,
#                     o.tenant_id,
#                     o.product_id,
#                     o.buyer_email,
#                     o.stripe_session_id,
#                     o.status,
#                     o.created_at
#                   from orders o
#                  where {where_sql}
#                  order by o.created_at desc, o.id desc
#                  limit :limit offset :offset
#                 """
#             ),
#             params,
#         ).fetchall()

#     items: List[dict] = []
#     for r in rows or []:
#         created_at = r[6].isoformat() if getattr(r[6], "isoformat", None) else str(r[6])

#         base = {
#             "id": int(r[0]),
#             "tenant_id": int(r[1]) if r[1] is not None else None,
#             "product_id": int(r[2]) if r[2] is not None else None,
#             "buyer_email": r[3],
#             "stripe_session_id": r[4],
#             "status": r[5],
#             "created_at": created_at,
#         }

#         if include_product:
#             base["product"] = {
#                 "slug": r[7],
#                 "title": r[8],
#                 "image_url": r[9],
#                 "price": str(r[10]) if r[10] is not None else None,
#                 "discounted_price": str(r[11]) if r[11] is not None else None,
#                 "currency": r[12],
#             }

#         items.append(base)

#     total_pages = (int(total) + int(page_size) - 1) // int(page_size)

#     return {
#         "ok": True,
#         "tenant_id": int(tenant_id),
#         "page": int(page),
#         "page_size": int(page_size),
#         "total": int(total),
#         "total_pages": int(total_pages),
#         "items": items,
#     }


# # -----------------------------
# # Optional: list enrollments for an order
# # -----------------------------
# @router.get("/orders/{order_id}/enrollments")
# def list_order_enrollments(
#     order_id: int,
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     db: Session = Depends(get_db),
# ):
#     """
#     Returns order_enrollments rows for this tenant+order.
#     Useful for debugging fulfillment.
#     """
#     rows = db.execute(
#         text(
#             """
#             select id, tenant_id, order_id, moodle_course_id, moodle_user_id, status, error, created_at
#               from order_enrollments
#              where tenant_id = :t
#                and order_id = :oid
#              order by created_at asc, id asc
#             """
#         ),
#         {"t": int(tenant_id), "oid": int(order_id)},
#     ).fetchall()

#     items = []
#     for r in rows or []:
#         items.append(
#             {
#                 "id": int(r[0]),
#                 "tenant_id": int(r[1]),
#                 "order_id": int(r[2]),
#                 "moodle_course_id": int(r[3]) if r[3] is not None else None,
#                 "moodle_user_id": int(r[4]) if r[4] is not None else None,
#                 "status": r[5],
#                 "error": r[6],
#                 "created_at": r[7].isoformat() if getattr(r[7], "isoformat", None) else str(r[7]),
#             }
#         )

#     return {"ok": True, "tenant_id": int(tenant_id), "order_id": int(order_id), "items": items}

# app/api/routes/orders.py
#
# Optimized:
# - ✅ Removed per-request DDL + commits (_ensure_orders_table) -> MUST be handled by migrations
# - ✅ Single query for data + total via COUNT(*) OVER() (no separate COUNT query)
# - ✅ Consistent ordering for stable pagination
# - ✅ Avoids extra Python work, keeps row parsing tight
#
# Recommended indexes (run once in DB):
#   create index if not exists idx_orders_tenant_created_id on orders (tenant_id, created_at desc, id desc);
#   create index if not exists idx_orders_tenant_status_created on orders (tenant_id, status, created_at desc, id desc);
#   create index if not exists idx_orders_tenant_product on orders (tenant_id, product_id);
# Optional (fast LIKE %q%):
#   create extension if not exists pg_trgm;
#   create index if not exists idx_orders_buyer_email_trgm on orders using gin (lower(coalesce(buyer_email,'')) gin_trgm_ops);
#   create index if not exists idx_orders_stripe_session_trgm on orders using gin (lower(coalesce(stripe_session_id,'')) gin_trgm_ops);

from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request

router = APIRouter()


@router.get("/orders/paged")
def list_orders_paged(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status: Optional[str] = Query(None, description="Filter by order status (pending/paid/fulfilled/expired)"),
    q: Optional[str] = Query(None, description="Search by buyer_email or stripe_session_id"),
    include_product: bool = Query(True, description="If true, include basic product info"),
):
    offset = (page - 1) * page_size

    where_parts = ["o.tenant_id = :t"]
    params = {"t": int(tenant_id), "limit": int(page_size), "offset": int(offset)}

    st_clean = (status or "").strip().lower()
    if st_clean:
        where_parts.append("o.status = :st")
        params["st"] = st_clean

    q_clean = (q or "").strip().lower()
    if q_clean:
        params["q"] = f"%{q_clean}%"
        where_parts.append(
            "(lower(coalesce(o.buyer_email,'')) like :q or lower(coalesce(o.stripe_session_id,'')) like :q)"
        )

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
                    count(*) over() as total_count
                  from orders o
                 where {where_sql}
                 order by o.created_at desc, o.id desc
                 limit :limit offset :offset
            """),
            params,
        ).fetchall()

    # total_count is the same for every row; if no rows then total=0
    total = int(rows[0][-1]) if rows else 0
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    items: List[dict] = []
    if include_product:
        # indexes:
        # 0..6 order fields
        # 7..12 product fields
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
                    "product": {
                        "slug": r[7],
                        "title": r[8],
                        "image_url": r[9],
                        "price": str(r[10]) if r[10] is not None else None,
                        "discounted_price": str(r[11]) if r[11] is not None else None,
                        "currency": r[12],
                    },
                }
            )
    else:
        # indexes:
        # 0..6 order fields
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