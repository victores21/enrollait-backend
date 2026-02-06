# Optimized:
# - ✅ No DDL / _ensure_* calls inside requests (remove per-request CREATE/ALTER/COMMIT)
# - ✅ Single transaction per write using `with db.begin():`
# - ✅ Faster counts: single query using LEFT JOIN + GROUP BY (no second round-trip)
# - ✅ Sync endpoints (threadpool safe) + fewer Python loops
#
# Recommended indexes (run once in DB):
#   create index if not exists idx_categories_tenant_name on categories (tenant_id, name);
#   create index if not exists idx_categories_tenant_slug on categories (tenant_id, slug);
#   create index if not exists idx_product_categories_tenant_category on product_categories (tenant_id, category_id);
# Optional (fast LIKE %q%):
#   create extension if not exists pg_trgm;
#   create index if not exists idx_categories_name_trgm on categories using gin (lower(name) gin_trgm_ops);
#   create index if not exists idx_categories_slug_trgm on categories using gin (lower(slug) gin_trgm_ops);

from __future__ import annotations

import re
from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request

router = APIRouter()

_slug_re = re.compile(r"[^a-z0-9-]+")


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("_", "-").replace(" ", "-")
    value = _slug_re.sub("", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "category"


# -----------------------------
# Schemas
# -----------------------------
class CategoryOut(BaseModel):
    id: int
    name: str
    slug: str
    created_at: str
    products_count: int | None = None


class CategoriesListOut(BaseModel):
    ok: bool
    tenant_id: int
    total: int
    items: list[CategoryOut]


class CreateCategoryPayload(BaseModel):
    name: str
    moodle_category_id: int | None = None


# -----------------------------
# Routes
# -----------------------------
@router.get("/categories", response_model=CategoriesListOut)
def list_categories(
    tenant_id: int = Depends(get_tenant_id_from_request),
    search: str | None = Query(None, description="Search by name/slug"),
    include_counts: bool = Query(False, description="Include products_count per category"),
    db: Session = Depends(get_db),
):
    where = ["c.tenant_id = :t"]
    params = {"t": tenant_id}

    if search and search.strip():
        params["q"] = f"%{search.strip().lower()}%"
        where.append("(lower(c.name) like :q or lower(c.slug) like :q)")

    where_sql = " and ".join(where)

    # If include_counts, do it in one query with LEFT JOIN.
    if include_counts:
        rows = db.execute(
            text(f"""
                select
                    c.id, c.name, c.slug, c.created_at,
                    count(pc.product_id)::int as products_count
                  from categories c
                  left join product_categories pc
                    on pc.tenant_id = c.tenant_id
                   and pc.category_id = c.id
                 where {where_sql}
                 group by c.id, c.name, c.slug, c.created_at
                 order by c.name asc
            """),
            params,
        ).fetchall()

        items = [
            {
                "id": int(r[0]),
                "name": str(r[1]),
                "slug": str(r[2]),
                "created_at": str(r[3]),
                "products_count": int(r[4] or 0),
            }
            for r in rows
        ]
    else:
        rows = db.execute(
            text(f"""
                select c.id, c.name, c.slug, c.created_at
                  from categories c
                 where {where_sql}
                 order by c.name asc
            """),
            params,
        ).fetchall()

        items = [
            {
                "id": int(r[0]),
                "name": str(r[1]),
                "slug": str(r[2]),
                "created_at": str(r[3]),
                "products_count": None,
            }
            for r in rows
        ]

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "total": len(items),
        "items": items,
    }


@router.post("/categories", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CreateCategoryPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    slug = slugify(name)

    try:
        with db.begin():
            row = db.execute(
                text("""
                    insert into categories (tenant_id, name, slug, moodle_category_id, created_at)
                    values (:t, :name, :slug, :mid, now())
                    returning id, name, slug, created_at, moodle_category_id
                """),
                {"t": tenant_id, "name": name, "slug": slug, "mid": payload.moodle_category_id},
            ).fetchone()

    except IntegrityError as e:
        # Most common: unique(tenant_id, slug)
        msg = str(getattr(e, "orig", e))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Category already exists for this tenant (same slug).",
                "tenant_id": tenant_id,
                "slug": slug,
                "error": msg,
            },
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": "DB error creating category", "error": f"{type(e).__name__}: {str(e)}"},
        )

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "category": {
            "id": int(row[0]),
            "name": row[1],
            "slug": row[2],
            "created_at": str(row[3]),
            "moodle_category_id": int(row[4]) if row[4] is not None else None,
        },
    }