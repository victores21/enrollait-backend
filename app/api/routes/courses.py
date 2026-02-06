# # app/api/routes/courses.py

# from datetime import datetime
# from fastapi import APIRouter, Depends, Query
# from pydantic import BaseModel
# from sqlalchemy import text
# from sqlalchemy.orm import Session

# from app.core.db import get_db
# from app.core.tenant import get_tenant_id_from_request  # ✅ tenant resolver

# router = APIRouter()


# # -----------------------------
# # DB helper
# # -----------------------------
# def _ensure_courses_table(db: Session) -> None:
#     """
#     Your project likely already has this table from Moodle sync.
#     This makes the router self-contained and safe to import.
#     """
#     db.execute(
#         text(
#             """
#             create table if not exists courses (
#               id bigserial primary key,
#               tenant_id bigint not null references tenants(id) on delete cascade,
#               moodle_course_id bigint not null,
#               fullname text not null,
#               summary text,
#               updated_at timestamptz not null default now(),
#               unique (tenant_id, moodle_course_id)
#             );
#             """
#         )
#     )
#     db.commit()


# # -----------------------------
# # Schemas
# # -----------------------------
# class CourseOut(BaseModel):
#     id: int
#     tenant_id: int
#     moodle_course_id: int
#     fullname: str
#     summary: str | None = None
#     updated_at: str


# class CoursesListOut(BaseModel):
#     ok: bool
#     tenant_id: int
#     total: int
#     items: list[CourseOut]


# # -----------------------------
# # Routes
# # -----------------------------
# @router.get("/courses", response_model=CoursesListOut)
# def list_courses(
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     search: str | None = Query(None, description="Search by fullname/summary"),
#     include_site_course: bool = Query(
#         False,
#         description="If true, include Moodle course id=1 (site/front page course).",
#     ),
#     order: str = Query(
#         "updated_desc",
#         description="updated_desc | updated_asc | name_asc | name_desc",
#     ),
#     limit: int = Query(500, ge=1, le=2000),
#     db: Session = Depends(get_db),
# ):
#     _ensure_courses_table(db)

#     where = ["tenant_id = :t"]
#     params = {"t": tenant_id, "limit": limit}

#     if not include_site_course:
#         where.append("moodle_course_id <> 1")

#     if search and search.strip():
#         params["q"] = f"%{search.strip()}%"
#         where.append("(fullname ILIKE :q OR coalesce(summary,'') ILIKE :q)")

#     where_sql = " and ".join(where)

#     order_map = {
#         "updated_desc": "updated_at desc, fullname asc",
#         "updated_asc": "updated_at asc, fullname asc",
#         "name_asc": "fullname asc",
#         "name_desc": "fullname desc",
#     }
#     order_sql = order_map.get(order, order_map["updated_desc"])

#     rows = db.execute(
#         text(
#             f"""
#             select id, tenant_id, moodle_course_id, fullname, summary, updated_at
#               from courses
#              where {where_sql}
#              order by {order_sql}
#              limit :limit
#             """
#         ),
#         params,
#     ).fetchall()

#     items: list[dict] = []
#     for r in rows:
#         updated = r[5]
#         updated_at = updated.isoformat() if isinstance(updated, datetime) else str(updated)
#         items.append(
#             {
#                 "id": int(r[0]),
#                 "tenant_id": int(r[1]),
#                 "moodle_course_id": int(r[2]),
#                 "fullname": str(r[3]),
#                 "summary": (str(r[4]) if r[4] is not None else None),
#                 "updated_at": updated_at,
#             }
#         )

#     return {
#         "ok": True,
#         "tenant_id": int(tenant_id),
#         "total": len(items),
#         "items": items,
#     }

# app/api/routes/courses.py
#
# Optimized:
# - ✅ Removed per-request DDL + commit (_ensure_courses_table) -> do migrations once
# - ✅ Safer search: ILIKE uses parameter; trims + lower-case not needed for ILIKE
# - ✅ Validates order param (prevents accidental slow/unindexed sorts + SQL injection risk)
# - ✅ Keeps query simple and fast, minimal Python work
#
# Recommended indexes (run once in DB):
#   create index if not exists idx_courses_tenant_updated on courses (tenant_id, updated_at desc);
#   create index if not exists idx_courses_tenant_name on courses (tenant_id, fullname);
#   create index if not exists idx_courses_tenant_moodle on courses (tenant_id, moodle_course_id);
#
# Optional (fast search on fullname/summary):
#   create extension if not exists pg_trgm;
#   create index if not exists idx_courses_fullname_trgm on courses using gin (fullname gin_trgm_ops);
#   create index if not exists idx_courses_summary_trgm on courses using gin (coalesce(summary,'') gin_trgm_ops);

from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request

router = APIRouter()


# -----------------------------
# Schemas
# -----------------------------
class CourseOut(BaseModel):
    id: int
    tenant_id: int
    moodle_course_id: int
    fullname: str
    summary: str | None = None
    updated_at: str


class CoursesListOut(BaseModel):
    ok: bool
    tenant_id: int
    total: int
    items: list[CourseOut]


# -----------------------------
# Routes
# -----------------------------
@router.get("/courses", response_model=CoursesListOut)
def list_courses(
    tenant_id: int = Depends(get_tenant_id_from_request),
    search: str | None = Query(None, description="Search by fullname/summary"),
    include_site_course: bool = Query(
        False,
        description="If true, include Moodle course id=1 (site/front page course).",
    ),
    order: str = Query(
        "updated_desc",
        description="updated_desc | updated_asc | name_asc | name_desc",
    ),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    where = ["tenant_id = :t"]
    params = {"t": int(tenant_id), "limit": int(limit)}

    if not include_site_course:
        where.append("moodle_course_id <> 1")

    q = (search or "").strip()
    if q:
        # ILIKE is already case-insensitive; use %...% for contains
        params["q"] = f"%{q}%"
        where.append("(fullname ILIKE :q OR coalesce(summary,'') ILIKE :q)")

    where_sql = " and ".join(where)

    # Only allow known orderings (keeps SQL safe and predictable)
    order_map = {
        "updated_desc": "updated_at desc, fullname asc, id asc",
        "updated_asc": "updated_at asc, fullname asc, id asc",
        "name_asc": "fullname asc, id asc",
        "name_desc": "fullname desc, id asc",
    }
    order_sql = order_map.get(order)
    if not order_sql:
        raise HTTPException(status_code=400, detail=f"Invalid order. Use one of: {', '.join(order_map.keys())}")

    rows = db.execute(
        text(f"""
            select id, tenant_id, moodle_course_id, fullname, summary, updated_at
              from courses
             where {where_sql}
             order by {order_sql}
             limit :limit
        """),
        params,
    ).fetchall()

    items = [
        {
            "id": int(r[0]),
            "tenant_id": int(r[1]),
            "moodle_course_id": int(r[2]),
            "fullname": str(r[3]),
            "summary": (str(r[4]) if r[4] is not None else None),
            "updated_at": (r[5].isoformat() if isinstance(r[5], datetime) else str(r[5])),
        }
        for r in (rows or [])
    ]

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "total": len(items),
        "items": items,
    }