# app/api/routes/kpis.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request

router = APIRouter()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_range(days: int = 30) -> tuple[datetime, datetime]:
    end = _utc_now()
    start = end - timedelta(days=days)
    return start, end


@router.get("/kpis/summary")
def kpis_summary(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),

    # range
    date_from: Optional[datetime] = Query(None, description="UTC datetime inclusive"),
    date_to: Optional[datetime] = Query(None, description="UTC datetime exclusive"),

    # defaults
    default_days: int = Query(30, ge=1, le=365, description="Used if date_from/date_to not provided"),

    # what counts as "paid revenue"
    revenue_statuses: str = Query("paid,fulfilled", description="Comma-separated statuses counted as revenue"),
) -> Dict[str, Any]:
    """
    Course marketplace KPIs for a time range:

    Revenue:
      - gross revenue (sum total_cents for paid statuses)

    Orders funnel:
      - total_orders_count (includes pending)
      - paid_orders_count (status in revenue_statuses)
      - fulfilled_orders_count (status='fulfilled')
      - checkout_conversion_rate = paid_orders / total_orders
      - fulfillment_rate = fulfilled_orders / paid_orders

    Customers:
      - new_paying_customers_count = distinct buyer_email among paid orders

    Students (operational):
      - new_student_accounts_count = count(user_map rows created in range)
    """
    if not date_from or not date_to:
        date_from, date_to = _default_range(default_days)

    statuses = [s.strip().lower() for s in (revenue_statuses or "").split(",") if s.strip()]
    if not statuses:
        statuses = ["paid", "fulfilled"]

    row = db.execute(
        text("""
            with base_orders as (
              select
                o.id,
                lower(coalesce(o.status,'')) as status,
                o.total_cents,
                nullif(lower(coalesce(o.buyer_email,'')), '') as buyer_email
              from orders o
              where o.tenant_id = :t
                and o.created_at >= :df
                and o.created_at <  :dt
            ),
            paid_orders as (
              select *
              from base_orders
              where status = any(:statuses)
            ),
            fulfilled_orders as (
              select *
              from base_orders
              where status = 'fulfilled'
            )
            select
              -- revenue
              (select coalesce(sum(total_cents), 0) from paid_orders) as revenue_cents,

              -- counts
              (select count(*) from base_orders) as total_orders_count,
              (select count(*) from paid_orders) as paid_orders_count,
              (select count(*) from fulfilled_orders) as fulfilled_orders_count,

              -- customers
              (select count(distinct buyer_email) from paid_orders where buyer_email is not null) as new_paying_customers_count
        """),
        {
            "t": int(tenant_id),
            "df": date_from,
            "dt": date_to,
            "statuses": statuses,
        },
    ).fetchone()

    revenue_cents = int(row[0] or 0)
    total_orders_count = int(row[1] or 0)
    paid_orders_count = int(row[2] or 0)
    fulfilled_orders_count = int(row[3] or 0)
    new_paying_customers_count = int(row[4] or 0)

    checkout_conversion_rate = None
    if total_orders_count > 0:
        checkout_conversion_rate = paid_orders_count / total_orders_count

    fulfillment_rate = None
    if paid_orders_count > 0:
        fulfillment_rate = fulfilled_orders_count / paid_orders_count

    # Operational: student accounts created (from user_map)
    stu_row = db.execute(
        text("""
            select count(*) as new_student_accounts_count
            from user_map um
            where um.tenant_id = :t
              and um.created_at >= :df
              and um.created_at <  :dt
        """),
        {"t": int(tenant_id), "df": date_from, "dt": date_to},
    ).fetchone()

    new_student_accounts_count = int(stu_row[0] or 0)

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "range": {"from": date_from.isoformat(), "to": date_to.isoformat()},

        "revenue": {
            "revenue_cents": revenue_cents,
            "paid_orders_count": paid_orders_count,
        },

        # ✅ Ecommerce-style funnel conversion (includes pending orders)
        "orders": {
            "total_orders_count": total_orders_count,
            "paid_orders_count": paid_orders_count,
            "fulfilled_orders_count": fulfilled_orders_count,
            "checkout_conversion_rate": checkout_conversion_rate,
            "fulfillment_rate": fulfillment_rate,
        },

        # ✅ Unique buyers who paid
        "customers": {
            "new_paying_customers_count": new_paying_customers_count,
        },

        # ✅ Operational (keep if you want it)
        "students": {
            "new_student_accounts_count": new_student_accounts_count,
        },

        "meta": {
            "revenue_statuses": statuses,
        },
    }


# @router.get("/kpis/revenue/daily")
# def kpis_revenue_daily(
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     db: Session = Depends(get_db),

#     days: int = Query(30, ge=1, le=365),
#     revenue_statuses: str = Query("paid,fulfilled", description="Comma-separated statuses counted as revenue"),
# ) -> Dict[str, Any]:
#     end = _utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
#     start = end - timedelta(days=days)

#     statuses = [s.strip().lower() for s in (revenue_statuses or "").split(",") if s.strip()]
#     if not statuses:
#         statuses = ["paid", "fulfilled"]

#     rows = db.execute(
#         text("""
#             select
#               date_trunc('day', o.created_at) as day,
#               coalesce(sum(o.total_cents), 0) as revenue_cents,
#               count(*) as orders_count
#             from orders o
#             where o.tenant_id = :t
#               and o.created_at >= :start
#               and o.created_at <  :end
#               and lower(o.status) = any(:statuses)
#             group by 1
#             order by 1 asc
#         """),
#         {"t": int(tenant_id), "start": start, "end": end, "statuses": statuses},
#     ).fetchall()

#     items: List[Dict[str, Any]] = []
#     for r in rows or []:
#         day = r[0]
#         items.append(
#             {
#                 "day": day.date().isoformat() if hasattr(day, "date") else str(day),
#                 "revenue_cents": int(r[1] or 0),
#                 "orders_count": int(r[2] or 0),
#             }
#         )

#     return {
#         "ok": True,
#         "tenant_id": int(tenant_id),
#         "range": {"from": start.isoformat(), "to": end.isoformat()},
#         "items": items,
#         "meta": {"revenue_statuses": statuses},
#     }

@router.get("/kpis/revenue/daily")
def kpis_revenue_daily(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
    revenue_statuses: str = Query("paid,fulfilled", description="Comma-separated statuses counted as revenue"),
) -> Dict[str, Any]:
    # ✅ include today by using tomorrow 00:00 as exclusive end
    end = (_utc_now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)

    statuses = [s.strip().lower() for s in (revenue_statuses or "").split(",") if s.strip()]
    if not statuses:
        statuses = ["paid", "fulfilled"]

    rows = db.execute(
        text("""
            select
              date_trunc('day', o.created_at) as day,
              coalesce(sum(o.total_cents), 0) as revenue_cents,
              count(*) as orders_count
            from orders o
            where o.tenant_id = :t
              and o.created_at >= :start
              and o.created_at <  :end
              and lower(coalesce(o.status,'')) = any(:statuses)
            group by 1
            order by 1 asc
        """),
        {"t": int(tenant_id), "start": start, "end": end, "statuses": statuses},
    ).fetchall()

    items: List[Dict[str, Any]] = []
    for r in rows or []:
        day = r[0]
        items.append(
            {
                "day": day.date().isoformat() if hasattr(day, "date") else str(day),
                "revenue_cents": int(r[1] or 0),
                "orders_count": int(r[2] or 0),
            }
        )

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "items": items,
        "meta": {"revenue_statuses": statuses},
    }


# @router.get("/kpis/students/daily")
# def kpis_students_daily(
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     db: Session = Depends(get_db),

#     days: int = Query(30, ge=1, le=365),
# ) -> Dict[str, Any]:
#     end = _utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
#     start = end - timedelta(days=days)

#     rows = db.execute(
#         text("""
#             select
#               date_trunc('day', um.created_at) as day,
#               count(*) as new_students_count
#             from user_map um
#             where um.tenant_id = :t
#               and um.created_at >= :start
#               and um.created_at <  :end
#             group by 1
#             order by 1 asc
#         """),
#         {"t": int(tenant_id), "start": start, "end": end},
#     ).fetchall()

#     items: List[Dict[str, Any]] = []
#     for r in rows or []:
#         day = r[0]
#         items.append(
#             {
#                 "day": day.date().isoformat() if hasattr(day, "date") else str(day),
#                 "new_students_count": int(r[1] or 0),
#             }
#         )

#     return {
#         "ok": True,
#         "tenant_id": int(tenant_id),
#         "range": {"from": start.isoformat(), "to": end.isoformat()},
#         "items": items,
#     }

@router.get("/kpis/students/daily")
def kpis_students_daily(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
) -> Dict[str, Any]:
    # ✅ include today by using tomorrow 00:00 as exclusive end
    end = (_utc_now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)

    rows = db.execute(
        text("""
            select
              date_trunc('day', um.created_at) as day,
              count(*) as new_students_count
            from user_map um
            where um.tenant_id = :t
              and um.created_at >= :start
              and um.created_at <  :end
            group by 1
            order by 1 asc
        """),
        {"t": int(tenant_id), "start": start, "end": end},
    ).fetchall()

    items: List[Dict[str, Any]] = []
    for r in rows or []:
        day = r[0]
        items.append(
            {
                "day": day.date().isoformat() if hasattr(day, "date") else str(day),
                "new_students_count": int(r[1] or 0),
            }
        )

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "items": items,
    }