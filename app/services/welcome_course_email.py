from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.services.postmark_email import PostmarkEmailService
from app.email_templates.welcome_course import WELCOME_COURSE_HTML


def _simple_render_double_curly(template: str, vars: dict[str, Any]) -> str:
    def repl(m: re.Match) -> str:
        key = (m.group(1) or "").strip()
        val = vars.get(key, "")
        return "" if val is None else str(val)

    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, template)


def _get_order_core(db: Session, tenant_id: int, order_id: int) -> dict[str, Any] | None:
    row = db.execute(
        text("""
            select o.id, o.tenant_id, o.buyer_email, o.product_id,
                   t.name as tenant_name, t.moodle_url
              from orders o
              join tenants t on t.id = o.tenant_id
             where o.tenant_id = :t
               and o.id = :oid
             limit 1
        """),
        {"t": int(tenant_id), "oid": int(order_id)},
    ).fetchone()

    if not row:
        return None

    return {
        "order_id": int(row[0]),
        "tenant_id": int(row[1]),
        "buyer_email": (row[2] or "").strip().lower() or None,
        "product_id": int(row[3]) if row[3] is not None else None,
        "tenant_name": str(row[4]) if row[4] else "Enrollait",
        "moodle_url": str(row[5]).rstrip("/") if row[5] else None,
    }


def _get_course_name_for_product(db: Session, tenant_id: int, product_id: int) -> str:
    row = db.execute(
        text("""
            select c.fullname
              from product_courses pc
              join courses c
                on c.id = pc.course_id
               and c.tenant_id = pc.tenant_id
             where pc.tenant_id = :t
               and pc.product_id = :p
             order by pc.id asc
             limit 1
        """),
        {"t": int(tenant_id), "p": int(product_id)},
    ).fetchone()

    if row and row[0]:
        return str(row[0])
    return "your course"


def _moodle_login_url(moodle_url: str | None) -> str | None:
    if not moodle_url:
        return None
    base = moodle_url.rstrip("/")
    return f"{base}/login/index.php"


async def send_welcome_course_email_for_tenant(
    *,
    db: Session,
    tenant_id: int,
    order_id: int,
) -> dict[str, Any]:
    order = _get_order_core(db, int(tenant_id), int(order_id))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found for this tenant")

    if int(order["tenant_id"]) != int(tenant_id):
        raise HTTPException(status_code=403, detail="Order does not belong to tenant")

    if not order["buyer_email"]:
        raise HTTPException(status_code=400, detail="Order has no buyer_email")

    if not order["product_id"]:
        raise HTTPException(status_code=400, detail="Order has no product_id")

    course_name = _get_course_name_for_product(db, int(tenant_id), int(order["product_id"]))
    moodle_login = _moodle_login_url(order.get("moodle_url"))
    if not moodle_login:
        raise HTTPException(status_code=400, detail="Tenant Moodle URL is not configured")

    support_email = (os.getenv("SUPPORT_EMAIL") or "support@enrollait.com").strip()
    brand_address = (os.getenv("BRAND_ADDRESS") or "").strip()
    year = datetime.utcnow().year

    html = _simple_render_double_curly(
        WELCOME_COURSE_HTML,
        {
            "brand_name": order["tenant_name"],
            "brand_address": brand_address,
            "course_name": course_name,
            "buyer_email": order["buyer_email"],
            "moodle_login_url": moodle_login,
            "support_email": support_email,
            "year": year,
        },
    )

    subject = f"Welcome to {course_name} — set your Moodle password"

    svc = PostmarkEmailService.from_env()
    res = await svc.send(
        to_email=order["buyer_email"],
        subject=subject,
        html_body=html,
        tag="course-welcome",
        metadata={"tenant_id": str(tenant_id), "order_id": str(order_id)},
    )

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "order_id": int(order_id),
        "to": order["buyer_email"],
        "subject": subject,
        "postmark": res,
    }