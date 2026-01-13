from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel

from app.core.db import get_db

router = APIRouter()

class LinkCoursesPayload(BaseModel):
    moodle_course_ids: list[int]

def _ensure_product_courses_table(db: Session) -> None:
    db.execute(text("""
        create table if not exists product_courses (
          id bigserial primary key,
          tenant_id bigint not null references tenants(id) on delete cascade,
          product_id bigint not null references products(id) on delete cascade,
          moodle_course_id bigint not null,
          created_at timestamptz not null default now(),
          unique (tenant_id, product_id, moodle_course_id)
        );
    """))
    db.commit()

@router.post("/tenants/{tenant_id}/products/{product_id}/courses")
def link_courses_to_product(
    tenant_id: int,
    product_id: int,
    payload: LinkCoursesPayload,
    db: Session = Depends(get_db),
):
    """
    MVP behavior: REPLACE mapping (delete old + insert new).
    """
    _ensure_product_courses_table(db)

    ids = sorted({int(x) for x in payload.moodle_course_ids if int(x) > 0})
    if not ids:
        return {"ok": False, "message": "moodle_course_ids must contain at least one valid id"}

    # Safety: ensure product belongs to tenant
    prod = db.execute(
        text("select id from products where id=:p and tenant_id=:t"),
        {"p": product_id, "t": tenant_id},
    ).fetchone()
    if not prod:
        return {"ok": False, "message": "Product not found for this tenant"}

    # Replace mapping
    db.execute(
        text("delete from product_courses where tenant_id=:t and product_id=:p"),
        {"t": tenant_id, "p": product_id},
    )

    db.execute(
        text("""
            insert into product_courses (tenant_id, product_id, moodle_course_id)
            values (:t, :p, :c)
            on conflict (tenant_id, product_id, moodle_course_id) do nothing
        """),
        [{"t": tenant_id, "p": product_id, "c": cid} for cid in ids],
    )

    db.commit()

    return {"ok": True, "tenant_id": tenant_id, "product_id": product_id, "linked_courses": ids}