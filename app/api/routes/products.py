# app/api/routes/products.py

from decimal import Decimal, ROUND_HALF_UP
import re
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, condecimal
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db

router = APIRouter()


# -----------------------------
# Helpers
# -----------------------------
_slug_re = re.compile(r"[^a-z0-9-]+")


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("_", "-").replace(" ", "-")
    value = _slug_re.sub("", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "product"


def to_cents(price: Decimal) -> int:
    # Stripe expects integer cents. Avoid float bugs.
    return int((price * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _ensure_products_table(db: Session) -> None:
    """
    Keep your existing DB schema and safely add columns if missing.
    Based on your screenshot, products already exists with:
      - id, tenant_id, moodle_course_id (optional), slug, price_cents, currency, is_published, created_at
    We add:
      - price numeric(10,2)  (for decimals like 19.99)
      - title, description (optional UX fields, harmless if unused)
      - updated_at (optional)
      - unique constraint on (tenant_id, slug) for clean routing
    """
    db.execute(text("""
        create table if not exists products (
          id bigserial primary key
        );
    """))
    db.commit()

    # Core columns (some may already exist)
    db.execute(text("alter table products add column if not exists tenant_id bigint;"))
    db.execute(text("alter table products add column if not exists moodle_course_id bigint;"))  # optional single-course product support
    db.execute(text("alter table products add column if not exists slug text;"))
    db.execute(text("alter table products add column if not exists price_cents int;"))
    db.execute(text("alter table products add column if not exists currency text default 'usd';"))
    db.execute(text("alter table products add column if not exists is_published boolean default false;"))
    db.execute(text("alter table products add column if not exists created_at timestamptz default now();"))

    # New decimal column for UX/display price
    db.execute(text("alter table products add column if not exists price numeric(10,2);"))

    # Optional UX columns (won't break anything if you don't use them)
    db.execute(text("alter table products add column if not exists title text;"))
    db.execute(text("alter table products add column if not exists description text;"))

    # Optional updated_at
    db.execute(text("alter table products add column if not exists updated_at timestamptz default now();"))

    db.commit()

    # Backfill price from price_cents when price is null
    db.execute(text("""
        update products
           set price = (price_cents::numeric / 100.0)
         where price is null and price_cents is not null;
    """))
    db.commit()

    # Best-effort constraints (ignore if you don't want strict constraints yet)
    try:
        # Ensure a product slug is unique per tenant
        db.execute(text("""
            do $$
            begin
              if not exists (
                select 1
                  from pg_constraint
                 where conname = 'products_tenant_slug_uniq'
              ) then
                alter table products
                add constraint products_tenant_slug_uniq unique (tenant_id, slug);
              end if;
            end $$;
        """))
        db.commit()
    except Exception:
        db.rollback()

def _ensure_product_courses_table(db: Session):
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

# -----------------------------
# Schemas
# -----------------------------
class CreateProductPayload(BaseModel):
    # You can pass slug or title; slug will be generated if missing.
    slug: str | None = None
    title: str | None = None
    description: str | None = None

    # Decimal price like 19.99
    price: condecimal(max_digits=10, decimal_places=2)
    currency: str = "usd"
    is_published: bool = False

    # Optional: if you want to support "single course product" too
    moodle_course_id: int | None = None


class UpdateProductPayload(BaseModel):
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    price: condecimal(max_digits=10, decimal_places=2) | None = None
    currency: str | None = None
    is_published: bool | None = None
    moodle_course_id: int | None = None


# -----------------------------
# Routes
# -----------------------------
@router.post("/tenants/{tenant_id}/products")
def create_product(
    tenant_id: int,
    payload: CreateProductPayload,
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)

    # slug priority: payload.slug -> payload.title -> fallback
    raw = payload.slug or payload.title or "product"
    slug = slugify(raw)

    # Ensure not empty
    if not slug:
        return {"ok": False, "message": "slug/title required"}

    price = Decimal(str(payload.price))
    if price <= Decimal("0"):
        return {"ok": False, "message": "price must be > 0"}

    currency = (payload.currency or "usd").lower().strip()
    price_cents = to_cents(price)

    try:
        row = db.execute(
            text("""
                insert into products
                  (tenant_id, moodle_course_id, slug, title, description, price, price_cents, currency, is_published, updated_at)
                values
                  (:tenant_id, :moodle_course_id, :slug, :title, :description, :price, :price_cents, :currency, :is_published, now())
                returning
                  id, tenant_id, moodle_course_id, slug, title, description, price, price_cents, currency, is_published, created_at
            """),
            {
                "tenant_id": tenant_id,
                "moodle_course_id": payload.moodle_course_id,
                "slug": slug,
                "title": payload.title,
                "description": payload.description,
                "price": str(price),
                "price_cents": price_cents,
                "currency": currency,
                "is_published": bool(payload.is_published),
            },
        ).fetchone()
        db.commit()
    except Exception as e:
        db.rollback()
        return {"ok": False, "message": f"DB error: {type(e).__name__}: {str(e)}"}

    return {
        "ok": True,
        "product": {
            "id": int(row[0]),
            "tenant_id": int(row[1]),
            "moodle_course_id": int(row[2]) if row[2] is not None else None,
            "slug": row[3],
            "title": row[4],
            "description": row[5],
            "price": str(row[6]) if row[6] is not None else None,
            "price_cents": int(row[7]) if row[7] is not None else None,
            "currency": row[8],
            "is_published": bool(row[9]),
            "created_at": str(row[10]),
        },
    }


@router.get("/tenants/{tenant_id}/products/paged")
def list_products_paged(
    tenant_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    published_only: bool = True,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)

    offset = (page - 1) * page_size

    where = ["tenant_id = :t"]
    params = {"t": tenant_id, "limit": page_size, "offset": offset}

    if published_only:
        where.append("is_published = true")

    if search and search.strip():
        params["q"] = f"%{search.strip().lower()}%"
        # title might be null on older rows, so COALESCE
        where.append("(lower(slug) like :q or lower(coalesce(title,'')) like :q)")

    where_sql = " and ".join(where)

    total = db.execute(
        text(f"select count(*) from products where {where_sql}"),
        params,
    ).scalar() or 0

    rows = db.execute(
        text(f"""
            select id, tenant_id, moodle_course_id, slug, title, description, price, price_cents, currency, is_published, created_at
              from products
             where {where_sql}
             order by created_at desc
             limit :limit offset :offset
        """),
        params,
    ).fetchall()

    products = []
    for r in rows:
        products.append({
            "id": int(r[0]),
            "tenant_id": int(r[1]),
            "moodle_course_id": int(r[2]) if r[2] is not None else None,
            "slug": r[3],
            "title": r[4],
            "description": r[5],
            "price": str(r[6]) if r[6] is not None else None,
            "price_cents": int(r[7]) if r[7] is not None else None,
            "currency": r[8],
            "is_published": bool(r[9]),
            "created_at": str(r[10]),
        })

    total_pages = (total + page_size - 1) // page_size

    return {
        "ok": True,
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "total_pages": int(total_pages),
        "items": products,
    }


@router.get("/tenants/{tenant_id}/products/{product_id}")
def get_product_detail(
    tenant_id: int,
    product_id: int,
    include_courses: bool = True,
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)

    # Fetch product (tenant-scoped)
    row = db.execute(
        text("""
            select id, tenant_id, moodle_course_id, slug, title, description, price, price_cents, currency, is_published, created_at
              from products
             where tenant_id = :t and id = :id
             limit 1
        """),
        {"t": tenant_id, "id": product_id},
    ).fetchone()

    if not row:
        return {"ok": False, "message": "Product not found", "tenant_id": tenant_id, "product_id": product_id}

    product = {
        "id": int(row[0]),
        "tenant_id": int(row[1]),
        "moodle_course_id": int(row[2]) if row[2] is not None else None,
        "slug": row[3],
        "title": row[4],
        "description": row[5],
        "price": str(row[6]) if row[6] is not None else None,
        "price_cents": int(row[7]) if row[7] is not None else None,
        "currency": row[8],
        "is_published": bool(row[9]),
        "created_at": str(row[10]),
    }

    if not include_courses:
        return {"ok": True, "product": product}

    # Courses linked via product_courses (bundle)
    # If your schema already has product_courses table, ensure it exists.
    _ensure_product_courses_table(db)

    linked = db.execute(
        text("""
            select pc.moodle_course_id, c.fullname, c.summary
              from product_courses pc
              left join courses c
                on c.tenant_id = pc.tenant_id and c.moodle_course_id = pc.moodle_course_id
             where pc.tenant_id = :t and pc.product_id = :p
             order by pc.moodle_course_id asc
        """),
        {"t": tenant_id, "p": product_id},
    ).fetchall()

    courses = []
    for r in linked:
        courses.append({
            "moodle_course_id": int(r[0]),
            "fullname": r[1],
            "summary": r[2],
        })

    product["courses"] = courses

    return {"ok": True, "product": product}

@router.patch("/tenants/{tenant_id}/products/{product_id}")
def update_product(
    tenant_id: int,
    product_id: int,
    payload: UpdateProductPayload,
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)

    # Ensure product belongs to tenant
    prod = db.execute(
        text("select id from products where id=:p and tenant_id=:t"),
        {"p": product_id, "t": tenant_id},
    ).fetchone()
    if not prod:
        return {"ok": False, "message": "Product not found for this tenant"}

    updates = {}
    sets = []

    if payload.slug is not None:
        updates["slug"] = slugify(payload.slug)
        sets.append("slug = :slug")
    elif payload.title is not None:
        # If title changes and no slug provided, do not auto-change slug (avoid breaking URLs)

        pass

    if payload.title is not None:
        updates["title"] = payload.title
        sets.append("title = :title")

    if payload.description is not None:
        updates["description"] = payload.description
        sets.append("description = :description")

    if payload.price is not None:
        price = Decimal(str(payload.price))
        if price <= Decimal("0"):
            return {"ok": False, "message": "price must be > 0"}
        updates["price"] = str(price)
        updates["price_cents"] = to_cents(price)
        sets.append("price = :price")
        sets.append("price_cents = :price_cents")

    if payload.currency is not None:
        updates["currency"] = payload.currency.lower().strip()
        sets.append("currency = :currency")

    if payload.is_published is not None:
        updates["is_published"] = bool(payload.is_published)
        sets.append("is_published = :is_published")

    if payload.moodle_course_id is not None:
        updates["moodle_course_id"] = payload.moodle_course_id
        sets.append("moodle_course_id = :moodle_course_id")

    if not sets:
        return {"ok": False, "message": "No fields to update"}

    updates["tenant_id"] = tenant_id
    updates["product_id"] = product_id

    try:
        row = db.execute(
            text(f"""
                update products
                   set {", ".join(sets)}, updated_at = now()
                 where id = :product_id and tenant_id = :tenant_id
                returning id, tenant_id, moodle_course_id, slug, title, description, price, price_cents, currency, is_published, created_at
            """),
            updates,
        ).fetchone()
        db.commit()
    except Exception as e:
        db.rollback()
        return {"ok": False, "message": f"DB error: {type(e).__name__}: {str(e)}"}

    return {
        "ok": True,
        "product": {
            "id": int(row[0]),
            "tenant_id": int(row[1]),
            "moodle_course_id": int(row[2]) if row[2] is not None else None,
            "slug": row[3],
            "title": row[4],
            "description": row[5],
            "price": str(row[6]) if row[6] is not None else None,
            "price_cents": int(row[7]) if row[7] is not None else None,
            "currency": row[8],
            "is_published": bool(row[9]),
            "created_at": str(row[10]),
        },
    }