from decimal import Decimal, ROUND_HALF_UP
import re
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, condecimal
from sqlalchemy import text
from sqlalchemy.orm import Session
from collections import defaultdict

from app.core.db import get_db

router = APIRouter()

# -----------------------------
# Helpers
# -----------------------------
_slug_re = re.compile(r"[^a-z0-9-]+")

ALLOWED_STOCK_STATUSES = {"available", "not_available"}


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
    Keeps your existing schema and adds missing columns safely.
    Existing: id, tenant_id, moodle_course_id, slug, price_cents, currency, is_published,
              created_at, price, title, description, updated_at

    New columns:
      - discounted_price numeric(10,2)
      - identifier text
      - stock_status text default 'available'
    """
    db.execute(text("""
        create table if not exists products (
          id bigserial primary key
        );
    """))
    db.commit()

    # Existing columns (best-effort, safe)
    db.execute(text("alter table products add column if not exists tenant_id bigint;"))
    db.execute(text("alter table products add column if not exists moodle_course_id bigint;"))
    db.execute(text("alter table products add column if not exists slug text;"))
    db.execute(text("alter table products add column if not exists price_cents int;"))
    db.execute(text("alter table products add column if not exists currency text default 'usd';"))
    db.execute(text("alter table products add column if not exists is_published boolean default false;"))
    db.execute(text("alter table products add column if not exists created_at timestamptz default now();"))
    db.execute(text("alter table products add column if not exists updated_at timestamptz default now();"))
    db.execute(text("alter table products add column if not exists price numeric(10,2);"))
    db.execute(text("alter table products add column if not exists title text;"))
    db.execute(text("alter table products add column if not exists description text;"))

    # NEW columns
    db.execute(text("alter table products add column if not exists discounted_price numeric(10,2);"))
    db.execute(text("alter table products add column if not exists identifier text;"))
    db.execute(text("alter table products add column if not exists stock_status text not null default 'available';"))
    db.execute(text("alter table products add column if not exists image_url text;"))
    db.commit()

    # Backfill price from price_cents when price is null
    db.execute(text("""
        update products
           set price = (price_cents::numeric / 100.0)
         where price is null and price_cents is not null;
    """))
    db.commit()

    # Ensure slug uniqueness per tenant (best-effort)
    try:
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


def _ensure_related_products_table(db: Session):
    db.execute(text("""
        create table if not exists product_related (
          id bigserial primary key,
          tenant_id bigint not null references tenants(id) on delete cascade,
          product_id bigint not null references products(id) on delete cascade,
          related_product_id bigint not null references products(id) on delete cascade,
          created_at timestamptz not null default now(),
          unique (tenant_id, product_id, related_product_id),
          check (product_id <> related_product_id)
        );
    """))
    db.commit()


def _ensure_categories_tables(db: Session):
    # categories might already exist in your DB; this is safe.
    db.execute(text("""
        create table if not exists categories (
          id bigserial primary key,
          tenant_id bigint not null references tenants(id) on delete cascade,
          name text not null,
          slug text not null,
          created_at timestamptz not null default now(),
          unique (tenant_id, slug)
        );
    """))
    db.execute(text("""
        create table if not exists product_categories (
          id bigserial primary key,
          tenant_id bigint not null references tenants(id) on delete cascade,
          product_id bigint not null references products(id) on delete cascade,
          category_id bigint not null references categories(id) on delete cascade,
          created_at timestamptz not null default now(),
          unique (tenant_id, product_id, category_id)
        );
    """))
    db.commit()


def _parse_optional_price(value) -> Decimal | None:
    if value is None:
        return None
    d = Decimal(str(value))
    if d <= Decimal("0"):
        return None
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _normalize_category_ids(category_ids: list[int] | None) -> list[int] | None:
    if category_ids is None:
        return None
    ids = sorted({int(x) for x in category_ids if int(x) > 0})
    return ids  # can be [] meaning "clear all"


def _set_product_categories(db: Session, tenant_id: int, product_id: int, category_ids: list[int]) -> None:
    """
    MVP behavior: replace categories mapping for the product.
    - category_ids may be empty -> clears categories
    - validates category ids belong to the tenant
    NOTE: does NOT commit; caller controls transaction.
    """
    _ensure_categories_tables(db)

    # Always clear first (replace behavior)
    db.execute(
        text("delete from product_categories where tenant_id = :t and product_id = :p"),
        {"t": tenant_id, "p": product_id},
    )

    if not category_ids:
        return

    # Validate categories exist for tenant
    existing = db.execute(
        text("""
            select id
              from categories
             where tenant_id = :t
               and id = any(:ids)
        """),
        {"t": tenant_id, "ids": category_ids},
    ).fetchall()
    existing_ids = {int(r[0]) for r in existing}

    missing = [cid for cid in category_ids if cid not in existing_ids]
    if missing:
        raise ValueError(f"Invalid category_ids for tenant {tenant_id}: {missing}")

    db.execute(
        text("""
            insert into product_categories (tenant_id, product_id, category_id, created_at)
            values (:t, :p, :c, now())
            on conflict (tenant_id, product_id, category_id) do nothing
        """),
        [{"t": tenant_id, "p": product_id, "c": cid} for cid in category_ids],
    )


# -----------------------------
# Schemas
# -----------------------------
class CreateProductPayload(BaseModel):
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    price: condecimal(max_digits=10, decimal_places=2)
    discounted_price: condecimal(max_digits=10, decimal_places=2) | None = None

    currency: str = "usd"
    is_published: bool = False

    identifier: str | None = None
    stock_status: str = "available"

    moodle_course_id: int | None = None

    # NEW: set categories on create (ids from your categories table)
    category_ids: list[int] | None = None


class UpdateProductPayload(BaseModel):
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    price: condecimal(max_digits=10, decimal_places=2) | None = None
    discounted_price: condecimal(max_digits=10, decimal_places=2) | None = None

    currency: str | None = None
    is_published: bool | None = None

    identifier: str | None = None
    stock_status: str | None = None

    moodle_course_id: int | None = None

    # NEW: set categories on update (send [] to clear)
    category_ids: list[int] | None = None


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

    raw = payload.slug or payload.title or "product"
    slug = slugify(raw)
    if not slug:
        return {"ok": False, "message": "slug/title required"}

    price = Decimal(str(payload.price))
    if price <= Decimal("0"):
        return {"ok": False, "message": "price must be > 0"}

    discounted = _parse_optional_price(payload.discounted_price)
    if discounted is not None and discounted >= price:
        return {"ok": False, "message": "discounted_price must be < price"}

    stock_status = (payload.stock_status or "available").strip().lower()
    if stock_status not in ALLOWED_STOCK_STATUSES:
        return {"ok": False, "message": f"stock_status must be one of {sorted(ALLOWED_STOCK_STATUSES)}"}

    currency = (payload.currency or "usd").lower().strip()
    price_cents = to_cents(price)

    category_ids = _normalize_category_ids(payload.category_ids)

    try:
        row = db.execute(
            text("""
                insert into products
                  (tenant_id, moodle_course_id, slug, title, description, image_url,
                   price, discounted_price, price_cents, currency, is_published,
                   identifier, stock_status, updated_at)
                values
                  (:tenant_id, :moodle_course_id, :slug, :title, :description,
                   :image_url, :price, :discounted_price, :price_cents, :currency, :is_published,
                   :identifier, :stock_status, now())
                returning
                  id, tenant_id, moodle_course_id, slug, title, description,
                  price, discounted_price, price_cents, currency, is_published,
                  identifier, stock_status, created_at
            """),
            {
                "tenant_id": tenant_id,
                "moodle_course_id": payload.moodle_course_id,
                "slug": slug,
                "title": payload.title,
                "description": payload.description,
                "image_url": payload.image_url,
                "price": str(price),
                "discounted_price": str(discounted) if discounted is not None else None,
                "price_cents": price_cents,
                "currency": currency,
                "is_published": bool(payload.is_published),
                "identifier": (payload.identifier or None),
                "stock_status": stock_status,
            },
        ).fetchone()

        product_id = int(row[0])

        # NEW: categories on create
        if category_ids is not None:
            _set_product_categories(db, tenant_id, product_id, category_ids)

        db.commit()

    except ValueError as ve:
        db.rollback()
        return {"ok": False, "message": str(ve)}
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
            "image_url": row[6],
            "price": str(row[7]) if row[7] is not None else None,
            "discounted_price": str(row[8]) if row[8] is not None else None,
            "price_cents": int(row[9]) if row[9] is not None else None,
            "currency": row[10],
            "is_published": bool(row[10]),
            "identifier": row[11],
            "stock_status": row[12],
            "created_at": str(row[13]),
            # optional: echo what was set
            "category_ids": category_ids if category_ids is not None else None,
        },
    }


# @router.get("/tenants/{tenant_id}/products/paged")
# def list_products_paged(
#     tenant_id: int,
#     page: int = Query(1, ge=1),
#     page_size: int = Query(12, ge=1, le=100),
#     published_only: bool = True,
#     search: str | None = None,
#     db: Session = Depends(get_db),
# ):
#     _ensure_products_table(db)

#     offset = (page - 1) * page_size

#     where = ["tenant_id = :t"]
#     params = {"t": tenant_id, "limit": page_size, "offset": offset}

#     if published_only:
#         where.append("is_published = true")

#     if search and search.strip():
#         params["q"] = f"%{search.strip().lower()}%"
#         where.append("(lower(slug) like :q or lower(coalesce(title,'')) like :q)")

#     where_sql = " and ".join(where)

#     total = db.execute(
#         text(f"select count(*) from products where {where_sql}"),
#         params,
#     ).scalar() or 0

#     rows = db.execute(
#         text(f"""
#             select id, tenant_id, moodle_course_id, slug, title, description,
#                    price, discounted_price, price_cents, currency, is_published,
#                    identifier, stock_status, created_at
#               from products
#              where {where_sql}
#              order by created_at desc
#              limit :limit offset :offset
#         """),
#         params,
#     ).fetchall()

#     items = []
#     for r in rows:
#         items.append({
#             "id": int(r[0]),
#             "tenant_id": int(r[1]),
#             "moodle_course_id": int(r[2]) if r[2] is not None else None,
#             "slug": r[3],
#             "title": r[4],
#             "description": r[5],
#             "price": str(r[6]) if r[6] is not None else None,
#             "discounted_price": str(r[7]) if r[7] is not None else None,
#             "price_cents": int(r[8]) if r[8] is not None else None,
#             "currency": r[9],
#             "is_published": bool(r[10]),
#             "identifier": r[11],
#             "stock_status": r[12],
#             "created_at": str(r[13]),
#         })

#     total_pages = (total + page_size - 1) // page_size

#     return {
#         "ok": True,
#         "page": page,
#         "page_size": page_size,
#         "total": int(total),
#         "total_pages": int(total_pages),
#         "items": items,
#     }

@router.get("/tenants/{tenant_id}/products/paged")
def list_products_paged(
    tenant_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    published_only: bool = True,
    search: str | None = None,
    include_categories: bool = True,
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)
    if include_categories:
        _ensure_categories_tables(db)

    offset = (page - 1) * page_size

    where = ["tenant_id = :t"]
    params = {"t": tenant_id, "limit": page_size, "offset": offset}

    if published_only:
        where.append("is_published = true")

    if search and search.strip():
        params["q"] = f"%{search.strip().lower()}%"
        where.append("(lower(slug) like :q or lower(coalesce(title,'')) like :q)")

    where_sql = " and ".join(where)

    total = db.execute(
        text(f"select count(*) from products where {where_sql}"),
        params,
    ).scalar() or 0

    rows = db.execute(
        text(f"""
            select id, tenant_id, moodle_course_id, slug, title, description, image_url,
                   price, discounted_price, price_cents, currency, is_published,
                   identifier, stock_status, created_at
              from products
             where {where_sql}
             order by created_at desc
             limit :limit offset :offset
        """),
        params,
    ).fetchall()

    items = []
    product_ids: list[int] = []

    for r in rows:
        pid = int(r[0])
        product_ids.append(pid)
        items.append({
            "id": pid,
            "tenant_id": int(r[1]),
            "moodle_course_id": int(r[2]) if r[2] is not None else None,
            "slug": r[3],
            "title": r[4],
            "description": r[5],
            "image_url": r[6],
            "price": str(r[7]) if r[7] is not None else None,
            "discounted_price": str(r[8]) if r[8] is not None else None,
            "price_cents": int(r[9]) if r[9] is not None else None,
            "currency": r[10],
            "is_published": bool(r[11]),
            "identifier": r[12],
            "stock_status": r[13],
            "created_at": str(r[14]),
            "categories": [],
        })

    # Attach categories per product (single extra query, no N+1)
    if include_categories and product_ids:
        cat_rows = db.execute(
            text("""
                select pc.product_id, c.id, c.name, c.slug
                  from product_categories pc
                  join categories c
                    on c.id = pc.category_id
                   and c.tenant_id = pc.tenant_id
                 where pc.tenant_id = :t
                   and pc.product_id = any(:pids)
                 order by c.name asc
            """),
            {"t": tenant_id, "pids": product_ids},
        ).fetchall()

        cats_by_product: dict[int, list[dict]] = defaultdict(list)
        for pr in cat_rows:
            cats_by_product[int(pr[0])].append({
                "id": int(pr[1]),
                "name": pr[2],
                "slug": pr[3],
            })

        for item in items:
            item["categories"] = cats_by_product.get(item["id"], [])

    total_pages = (total + page_size - 1) // page_size

    return {
        "ok": True,
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "total_pages": int(total_pages),
        "items": items,
    }


@router.get("/tenants/{tenant_id}/products/{product_id}")
def get_product_detail(
    tenant_id: int,
    product_id: int,
    include_courses: bool = True,
    include_related: bool = True,
    include_categories: bool = True,
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)

    row = db.execute(
        text("""
            select id, tenant_id, moodle_course_id, slug, title, description, image_url,
                   price, discounted_price, price_cents, currency, is_published,
                   identifier, stock_status, created_at
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
        "image_url": row[6],
        "price": str(row[7]) if row[7] is not None else None,
        "discounted_price": str(row[8]) if row[8] is not None else None,
        "price_cents": int(row[9]) if row[9] is not None else None,
        "currency": row[10],
        "is_published": bool(row[11]),
        "identifier": row[12],
        "stock_status": row[13],
        "created_at": str(row[14]),
    }

    if include_courses:
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

        product["courses"] = [{
            "moodle_course_id": int(r[0]),
            "fullname": r[1],
            "summary": r[2],
        } for r in linked]

    if include_related:
        _ensure_related_products_table(db)
        related_rows = db.execute(
            text("""
                select p2.id, p2.slug, p2.title, p2.description, p2.image_url, p2.price, p2.discounted_price, p2.currency, p2.is_published, p2.stock_status
                  from product_related pr
                  join products p2
                    on p2.id = pr.related_product_id and p2.tenant_id = pr.tenant_id
                 where pr.tenant_id = :t and pr.product_id = :p
                 order by pr.created_at desc
            """),
            {"t": tenant_id, "p": product_id},
        ).fetchall()

        product["related_products"] = [{
            "id": int(r[0]),
            "slug": r[1],
            "title": r[2],
            "description": r[3],
            "image_url": r[4],
            "price": str(r[4]) if r[4] is not None else None,
            "discounted_price": str(r[5]) if r[5] is not None else None,
            "currency": r[6],
            "is_published": bool(r[7]),
            "stock_status": r[8],
        } for r in related_rows]

    if include_categories:
        _ensure_categories_tables(db)
        cat_rows = db.execute(
            text("""
                select c.id, c.name, c.slug
                  from product_categories pc
                  join categories c
                    on c.id = pc.category_id and c.tenant_id = pc.tenant_id
                 where pc.tenant_id = :t and pc.product_id = :p
                 order by c.name asc
            """),
            {"t": tenant_id, "p": product_id},
        ).fetchall()

        product["categories"] = [{
            "id": int(r[0]),
            "name": r[1],
            "slug": r[2],
        } for r in cat_rows]

    return {"ok": True, "product": product}


@router.patch("/tenants/{tenant_id}/products/{product_id}")
def update_product(
    tenant_id: int,
    product_id: int,
    payload: UpdateProductPayload,
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)

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

    if payload.title is not None:
        updates["title"] = payload.title
        sets.append("title = :title")

    if payload.description is not None:
        updates["description"] = payload.description
        sets.append("description = :description")

    if payload.image_url is not None:
        updates["image_url"] = payload.image_url.strip() if payload.image_url else None
        sets.append("image_url = :image_url")        

    if payload.price is not None:
        price = Decimal(str(payload.price))
        if price <= Decimal("0"):
            return {"ok": False, "message": "price must be > 0"}
        updates["price"] = str(price)
        updates["price_cents"] = to_cents(price)
        sets.append("price = :price")
        sets.append("price_cents = :price_cents")

    # discounted_price:
    # - If client wants to clear it, they must send discounted_price: null.
    #   But pydantic sets it to None, and you can't distinguish "not sent" vs "sent null"
    #   unless you use model_fields_set. We'll handle both:
    if "discounted_price" in getattr(payload, "model_fields_set", set()):
        discounted = _parse_optional_price(payload.discounted_price)
        current_price = None
        if payload.price is None:
            current_price = db.execute(
                text("select price from products where tenant_id=:t and id=:p"),
                {"t": tenant_id, "p": product_id},
            ).scalar()
        base_price = Decimal(str(payload.price)) if payload.price is not None else (Decimal(str(current_price)) if current_price is not None else None)
        if base_price is not None and discounted is not None and discounted >= base_price:
            return {"ok": False, "message": "discounted_price must be < price"}
        updates["discounted_price"] = str(discounted) if discounted is not None else None
        sets.append("discounted_price = :discounted_price")

    if payload.currency is not None:
        updates["currency"] = payload.currency.lower().strip()
        sets.append("currency = :currency")

    if payload.is_published is not None:
        updates["is_published"] = bool(payload.is_published)
        sets.append("is_published = :is_published")

    if payload.identifier is not None:
        updates["identifier"] = payload.identifier.strip() if payload.identifier else None
        sets.append("identifier = :identifier")

    if payload.stock_status is not None:
        stock_status = payload.stock_status.strip().lower()
        if stock_status not in ALLOWED_STOCK_STATUSES:
            return {"ok": False, "message": f"stock_status must be one of {sorted(ALLOWED_STOCK_STATUSES)}"}
        updates["stock_status"] = stock_status
        sets.append("stock_status = :stock_status")

    if payload.moodle_course_id is not None:
        updates["moodle_course_id"] = payload.moodle_course_id
        sets.append("moodle_course_id = :moodle_course_id")

    category_ids = _normalize_category_ids(payload.category_ids)

    if not sets and category_ids is None:
        return {"ok": False, "message": "No fields to update"}

    updates["tenant_id"] = tenant_id
    updates["product_id"] = product_id

    try:
        # Update product fields if any
        row = None
        if sets:
            row = db.execute(
                text(f"""
                    update products
                       set {", ".join(sets)}, updated_at = now()
                     where id = :product_id and tenant_id = :tenant_id
                    returning id, tenant_id, moodle_course_id, slug, title, description,
                              image_url, price, discounted_price, price_cents, currency, is_published,
                              identifier, stock_status, created_at
                """),
                updates,
            ).fetchone()

        # NEW: categories on update (send [] to clear)
        if category_ids is not None:
            _set_product_categories(db, tenant_id, product_id, category_ids)

        # If only categories were changed, fetch row now
        if row is None:
            row = db.execute(
                text("""
                    select id, tenant_id, moodle_course_id, slug, title, description,
                           image_url, price, discounted_price, price_cents, currency, is_published,
                           identifier, stock_status, created_at
                      from products
                     where tenant_id = :t and id = :p
                     limit 1
                """),
                {"t": tenant_id, "p": product_id},
            ).fetchone()

        db.commit()

    except ValueError as ve:
        db.rollback()
        return {"ok": False, "message": str(ve)}
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
            "image_url": row[6],
            "price": str(row[7]) if row[7] is not None else None,
            "discounted_price": str(row[8]) if row[8] is not None else None,
            "price_cents": int(row[9]) if row[9] is not None else None,
            "currency": row[10],
            "is_published": bool(row[11]),
            "identifier": row[12],
            "stock_status": row[13],
            "created_at": str(row[14]),
            "category_ids": category_ids if category_ids is not None else None,
        },
    }