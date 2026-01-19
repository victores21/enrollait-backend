# from decimal import Decimal, ROUND_HALF_UP
# import re
# from fastapi import APIRouter, Depends, Query, Form, UploadFile, File, HTTPException, status
# from pydantic import BaseModel, condecimal
# from sqlalchemy import text
# from sqlalchemy.orm import Session
# from sqlalchemy.exc import IntegrityError
# from collections import defaultdict
# import secrets
# from uuid import uuid4

# from app.core.db import get_db
# from app.core.tenant import get_tenant_id_from_request  # ✅ tenant resolver
# from app.core.supabase import upload_product_image

# router = APIRouter()

# # -----------------------------
# # Helpers
# # -----------------------------
# _slug_re = re.compile(r"[^a-z0-9-]+")

# ALLOWED_STOCK_STATUSES = {"available", "not_available"}


# def _ext_from_content_type(content_type: str) -> str:
#     ct = (content_type or "").lower()
#     if ct == "image/png":
#         return ".png"
#     if ct in ("image/jpeg", "image/jpg"):
#         return ".jpg"
#     if ct == "image/webp":
#         return ".webp"
#     return ""

# def _validate_image_bytes(image: UploadFile, data: bytes, max_mb: int = 5) -> None:
#     allowed = {"image/png", "image/jpeg", "image/webp"}
#     if not image.content_type or image.content_type.lower() not in allowed:
#         raise HTTPException(status_code=400, detail="image must be png, jpg, or webp")

#     max_bytes = max_mb * 1024 * 1024
#     if len(data) > max_bytes:
#         raise HTTPException(status_code=400, detail=f"image too large (max {max_mb}MB)")

# def _make_storage_key(tenant_id: int, product_id: int, content_type: str) -> str:
#     ext = _ext_from_content_type(content_type)
#     if not ext:
#         ext = ".bin"
#     # Example: tenants/2/products/10/0f1c...a2.webp
#     return f"tenants/{tenant_id}/products/{product_id}/{uuid4().hex}{ext}"


# def slugify(value: str) -> str:
#     value = (value or "").strip().lower()
#     value = value.replace("_", "-").replace(" ", "-")
#     value = _slug_re.sub("", value)
#     value = re.sub(r"-{2,}", "-", value).strip("-")
#     return value or "product"


# def to_cents(price: Decimal) -> int:
#     # Stripe expects integer cents. Avoid float bugs.
#     return int((price * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# def _ensure_products_table(db: Session) -> None:
#     """
#     Products table (no longer uses moodle_course_id; course linkage is via product_courses).
#     """
#     db.execute(text("""
#         create table if not exists products (
#           id bigserial primary key
#         );
#     """))
#     db.commit()

#     db.execute(text("alter table products add column if not exists tenant_id bigint;"))
#     # ❌ deprecated, do NOT add/use moodle_course_id anymore
#     # db.execute(text("alter table products add column if not exists moodle_course_id bigint;"))

#     db.execute(text("alter table products add column if not exists slug text;"))
#     db.execute(text("alter table products add column if not exists price_cents int;"))
#     db.execute(text("alter table products add column if not exists currency text default 'usd';"))
#     db.execute(text("alter table products add column if not exists is_published boolean default false;"))
#     db.execute(text("alter table products add column if not exists created_at timestamptz default now();"))
#     db.execute(text("alter table products add column if not exists updated_at timestamptz default now();"))
#     db.execute(text("alter table products add column if not exists price numeric(10,2);"))
#     db.execute(text("alter table products add column if not exists title text;"))
#     db.execute(text("alter table products add column if not exists description text;"))

#     db.execute(text("alter table products add column if not exists discounted_price numeric(10,2);"))
#     db.execute(text("alter table products add column if not exists identifier text;"))
#     db.execute(text("alter table products add column if not exists stock_status text not null default 'available';"))
#     db.execute(text("alter table products add column if not exists image_url text;"))
#     db.commit()

#     # Backfill price from price_cents when price is null
#     db.execute(text("""
#         update products
#            set price = (price_cents::numeric / 100.0)
#          where price is null and price_cents is not null;
#     """))
#     db.commit()

#     # Ensure slug uniqueness per tenant (best-effort)
#     try:
#         db.execute(text("""
#             do $$
#             begin
#               if not exists (
#                 select 1
#                   from pg_constraint
#                  where conname = 'products_tenant_slug_uniq'
#               ) then
#                 alter table products
#                 add constraint products_tenant_slug_uniq unique (tenant_id, slug);
#               end if;
#             end $$;
#         """))
#         db.commit()
#     except Exception:
#         db.rollback()


# def _ensure_courses_table(db: Session) -> None:
#     """
#     Minimal ensure (your project likely already creates this in moodle routes,
#     but this makes this router self-contained/safe).
#     """
#     db.execute(text("""
#         create table if not exists courses (
#           id bigserial primary key,
#           tenant_id bigint not null references tenants(id) on delete cascade,
#           moodle_course_id bigint not null,
#           fullname text not null,
#           summary text,
#           updated_at timestamptz not null default now(),
#           unique (tenant_id, moodle_course_id)
#         );
#     """))
#     db.commit()


# def _ensure_product_courses_table(db: Session):
#     db.execute(text("""
#         create table if not exists product_courses (
#           id bigserial primary key,
#           tenant_id bigint not null references tenants(id) on delete cascade,
#           product_id bigint not null references products(id) on delete cascade,
#           moodle_course_id bigint not null,
#           created_at timestamptz not null default now(),
#           unique (tenant_id, product_id, moodle_course_id)
#         );
#     """))
#     db.commit()


# def _ensure_related_products_table(db: Session):
#     db.execute(text("""
#         create table if not exists product_related (
#           id bigserial primary key,
#           tenant_id bigint not null references tenants(id) on delete cascade,
#           product_id bigint not null references products(id) on delete cascade,
#           related_product_id bigint not null references products(id) on delete cascade,
#           created_at timestamptz not null default now(),
#           unique (tenant_id, product_id, related_product_id),
#           check (product_id <> related_product_id)
#         );
#     """))
#     db.commit()


# def _ensure_categories_tables(db: Session):
#     # categories might already exist in your DB; this is safe.
#     db.execute(text("""
#         create table if not exists categories (
#           id bigserial primary key,
#           tenant_id bigint not null references tenants(id) on delete cascade,
#           name text not null,
#           slug text not null,
#           created_at timestamptz not null default now(),
#           unique (tenant_id, slug)
#         );
#     """))
#     db.execute(text("""
#         create table if not exists product_categories (
#           id bigserial primary key,
#           tenant_id bigint not null references tenants(id) on delete cascade,
#           product_id bigint not null references products(id) on delete cascade,
#           category_id bigint not null references categories(id) on delete cascade,
#           created_at timestamptz not null default now(),
#           unique (tenant_id, product_id, category_id)
#         );
#     """))
#     db.commit()


# def _parse_optional_price(value) -> Decimal | None:
#     if value is None:
#         return None
#     d = Decimal(str(value))
#     if d <= Decimal("0"):
#         return None
#     return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# def _normalize_category_ids(category_ids: list[int] | None) -> list[int] | None:
#     if category_ids is None:
#         return None
#     ids = sorted({int(x) for x in category_ids if int(x) > 0})
#     return ids  # can be [] meaning "clear all"


# def _set_product_categories(db: Session, tenant_id: int, product_id: int, category_ids: list[int]) -> None:
#     """
#     Replace categories mapping for the product.
#     - category_ids may be empty -> clears categories
#     - validates category ids belong to the tenant
#     NOTE: does NOT commit; caller controls transaction.
#     """
#     _ensure_categories_tables(db)

#     db.execute(
#         text("delete from product_categories where tenant_id = :t and product_id = :p"),
#         {"t": tenant_id, "p": product_id},
#     )

#     if not category_ids:
#         return

#     existing = db.execute(
#         text("""
#             select id
#               from categories
#              where tenant_id = :t
#                and id = any(:ids)
#         """),
#         {"t": tenant_id, "ids": category_ids},
#     ).fetchall()
#     existing_ids = {int(r[0]) for r in existing}

#     missing = [cid for cid in category_ids if cid not in existing_ids]
#     if missing:
#         raise ValueError(f"Invalid category_ids for tenant {tenant_id}: {missing}")

#     db.execute(
#         text("""
#             insert into product_categories (tenant_id, product_id, category_id, created_at)
#             values (:t, :p, :c, now())
#             on conflict (tenant_id, product_id, category_id) do nothing
#         """),
#         [{"t": tenant_id, "p": product_id, "c": cid} for cid in category_ids],
#     )


# def _normalize_course_ids(course_ids: list[int] | None) -> list[int] | None:
#     if course_ids is None:
#         return None
#     ids = sorted({int(x) for x in course_ids if int(x) > 0})
#     return ids  # [] means "clear all"


# def _set_product_courses(db: Session, tenant_id: int, product_id: int, course_ids: list[int]) -> None:
#     """
#     Replace courses mapping for the product.
#     - course_ids may be empty -> clears mapping
#     - validates course ids belong to this tenant (must exist in courses table)
#     NOTE: does NOT commit; caller controls transaction.
#     """
#     _ensure_product_courses_table(db)
#     _ensure_courses_table(db)

#     db.execute(
#         text("delete from product_courses where tenant_id = :t and product_id = :p"),
#         {"t": tenant_id, "p": product_id},
#     )

#     if not course_ids:
#         return

#     existing = db.execute(
#         text("""
#             select moodle_course_id
#               from courses
#              where tenant_id = :t
#                and moodle_course_id = any(:ids)
#         """),
#         {"t": tenant_id, "ids": course_ids},
#     ).fetchall()
#     existing_ids = {int(r[0]) for r in existing}

#     missing = [cid for cid in course_ids if cid not in existing_ids]
#     if missing:
#         raise ValueError(f"Invalid course_ids for tenant {tenant_id}: {missing}")

#     db.execute(
#         text("""
#             insert into product_courses (tenant_id, product_id, moodle_course_id, created_at)
#             values (:t, :p, :c, now())
#             on conflict (tenant_id, product_id, moodle_course_id) do nothing
#         """),
#         [{"t": tenant_id, "p": product_id, "c": cid} for cid in course_ids],
#     )


# def _validate_image(image: UploadFile) -> None:
#     allowed = {"image/png", "image/jpeg", "image/webp"}
#     if not image.content_type or image.content_type.lower() not in allowed:
#         raise ValueError("image must be png, jpg, or webp")


# def _random_key(ext: str) -> str:
#     return secrets.token_hex(16) + ext


# # -----------------------------
# # Schemas
# # -----------------------------
# class CreateProductPayload(BaseModel):
#     title: str  # ✅ REQUIRED
#     description: str | None = None
#     image_url: str | None = None

#     price: condecimal(max_digits=10, decimal_places=2)  # ✅ REQUIRED
#     discounted_price: condecimal(max_digits=10, decimal_places=2) | None = None

#     currency: str = "usd"  # default usd
#     identifier: str | None = None
#     stock_status: str = "available"

#     # ✅ NEW
#     course_ids: list[int] | None = None

#     # ✅ existing
#     category_ids: list[int] | None = None


# class UpdateProductPayload(BaseModel):
#     title: str | None = None
#     description: str | None = None
#     image_url: str | None = None
#     price: condecimal(max_digits=10, decimal_places=2) | None = None
#     discounted_price: condecimal(max_digits=10, decimal_places=2) | None = None

#     currency: str | None = None
#     is_published: bool | None = None  # ✅ allow publishing on update

#     identifier: str | None = None
#     stock_status: str | None = None

#     # ✅ NEW: send [] to clear
#     course_ids: list[int] | None = None

#     # ✅ existing: send [] to clear
#     category_ids: list[int] | None = None


# # -----------------------------
# # Routes
# # -----------------------------
# @router.post("/products")
# def create_product(
#     payload: CreateProductPayload,
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     db: Session = Depends(get_db),
# ):
#     _ensure_products_table(db)

#     title = (payload.title or "").strip()
#     if not title:
#         raise HTTPException(status_code=400, detail="title is required")

#     slug = slugify(title)

#     price = Decimal(str(payload.price))
#     if price <= Decimal("0"):
#         raise HTTPException(status_code=400, detail="price must be > 0")

#     discounted = _parse_optional_price(payload.discounted_price)
#     if discounted is not None and discounted >= price:
#         raise HTTPException(status_code=400, detail="discounted_price must be < price")

#     stock_status = (payload.stock_status or "available").strip().lower()
#     if stock_status not in ALLOWED_STOCK_STATUSES:
#         raise HTTPException(
#             status_code=400,
#             detail=f"stock_status must be one of {sorted(ALLOWED_STOCK_STATUSES)}",
#         )

#     currency = (payload.currency or "usd").lower().strip() or "usd"
#     price_cents = to_cents(price)

#     category_ids = _normalize_category_ids(payload.category_ids)
#     course_ids = _normalize_course_ids(payload.course_ids)

#     try:
#         row = db.execute(
#             text("""
#                 insert into products
#                   (tenant_id, slug, title, description, image_url,
#                    price, discounted_price, price_cents, currency, is_published,
#                    identifier, stock_status, updated_at)
#                 values
#                   (:tenant_id, :slug, :title, :description,
#                    :image_url, :price, :discounted_price, :price_cents, :currency,
#                    false,
#                    :identifier, :stock_status, now())
#                 returning
#                   id, tenant_id, slug, title, description,
#                   image_url, price, discounted_price, price_cents, currency, is_published,
#                   identifier, stock_status, created_at
#             """),
#             {
#                 "tenant_id": tenant_id,
#                 "slug": slug,
#                 "title": title,
#                 "description": payload.description or None,
#                 "image_url": payload.image_url or None,
#                 "price": str(price),
#                 "discounted_price": str(discounted) if discounted is not None else None,
#                 "price_cents": price_cents,
#                 "currency": currency,
#                 "identifier": (payload.identifier or None),
#                 "stock_status": stock_status,
#             },
#         ).fetchone()

#         product_id = int(row[0])

#         if course_ids is not None:
#             _set_product_courses(db, tenant_id, product_id, course_ids)

#         if category_ids is not None:
#             _set_product_categories(db, tenant_id, product_id, category_ids)

#         db.commit()

#     except ValueError as ve:
#         db.rollback()
#         raise HTTPException(status_code=400, detail=str(ve))

#     except IntegrityError as ie:
#         db.rollback()
#         # Detect unique violation for (tenant_id, slug)
#         msg = str(getattr(ie, "orig", ie))
#         if "duplicate key value violates unique constraint" in msg and (
#             "products_tenant_id_slug_key" in msg or "products_tenant_slug_uniq" in msg
#         ):
#             raise HTTPException(
#                 status_code=status.HTTP_409_CONFLICT,
#                 detail={
#                     "message": "A product with this title/slug already exists for this tenant.",
#                     "tenant_id": tenant_id,
#                     "slug": slug,
#                     "hint": "Change the title or implement auto-suffixing (e.g. producto-test2-2).",
#                 },
#             )
#         # Other integrity errors (FK, null, etc.)
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail={"message": "Database integrity error", "error": msg},
#         )

#     except Exception as e:
#         db.rollback()
#         raise HTTPException(
#             status_code=500,
#             detail={"message": "DB error creating product", "error": f"{type(e).__name__}: {str(e)}"},
#         )

#     return {
#         "ok": True,
#         "product": {
#             "id": int(row[0]),
#             "tenant_id": int(row[1]),
#             "slug": row[2],
#             "title": row[3],
#             "description": row[4],
#             "image_url": row[5],
#             "price": str(row[6]) if row[6] is not None else None,
#             "discounted_price": str(row[7]) if row[7] is not None else None,
#             "price_cents": int(row[8]) if row[8] is not None else None,
#             "currency": row[9],
#             "is_published": bool(row[10]),
#             "identifier": row[11],
#             "stock_status": row[12],
#             "created_at": str(row[13]),
#             "course_ids": course_ids if course_ids is not None else None,
#             "category_ids": category_ids if category_ids is not None else None,
#         },
#     }


# @router.get("/products/paged")
# def list_products_paged(
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     page: int = Query(1, ge=1),
#     page_size: int = Query(12, ge=1, le=100),
#     published_only: bool = True,
#     search: str | None = None,
#     include_categories: bool = True,
#     db: Session = Depends(get_db),
# ):
#     _ensure_products_table(db)
#     if include_categories:
#         _ensure_categories_tables(db)

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
#             select id, tenant_id, slug, title, description, image_url,
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
#     product_ids: list[int] = []

#     for r in rows:
#         pid = int(r[0])
#         product_ids.append(pid)
#         items.append({
#             "id": pid,
#             "tenant_id": int(r[1]),
#             "slug": r[2],
#             "title": r[3],
#             "description": r[4],
#             "image_url": r[5],
#             "price": str(r[6]) if r[6] is not None else None,
#             "discounted_price": str(r[7]) if r[7] is not None else None,
#             "price_cents": int(r[8]) if r[8] is not None else None,
#             "currency": r[9],
#             "is_published": bool(r[10]),
#             "identifier": r[11],
#             "stock_status": r[12],
#             "created_at": str(r[13]),
#             "categories": [],
#         })

#     if include_categories and product_ids:
#         cat_rows = db.execute(
#             text("""
#                 select pc.product_id, c.id, c.name, c.slug
#                   from product_categories pc
#                   join categories c
#                     on c.id = pc.category_id
#                    and c.tenant_id = pc.tenant_id
#                  where pc.tenant_id = :t
#                    and pc.product_id = any(:pids)
#                  order by c.name asc
#             """),
#             {"t": tenant_id, "pids": product_ids},
#         ).fetchall()

#         cats_by_product: dict[int, list[dict]] = defaultdict(list)
#         for pr in cat_rows:
#             cats_by_product[int(pr[0])].append({
#                 "id": int(pr[1]),
#                 "name": pr[2],
#                 "slug": pr[3],
#             })

#         for item in items:
#             item["categories"] = cats_by_product.get(item["id"], [])

#     total_pages = (total + page_size - 1) // page_size

#     return {
#         "ok": True,
#         "tenant_id": tenant_id,
#         "page": page,
#         "page_size": page_size,
#         "total": int(total),
#         "total_pages": int(total_pages),
#         "items": items,
#     }


# @router.get("/products/{product_id}")
# def get_product_detail(
#     product_id: int,
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     include_courses: bool = True,
#     include_related: bool = True,
#     include_categories: bool = True,
#     db: Session = Depends(get_db),
# ):
#     _ensure_products_table(db)

#     row = db.execute(
#         text("""
#             select id, tenant_id, slug, title, description, image_url,
#                    price, discounted_price, price_cents, currency, is_published,
#                    identifier, stock_status, created_at
#               from products
#              where tenant_id = :t and id = :id
#              limit 1
#         """),
#         {"t": tenant_id, "id": product_id},
#     ).fetchone()

#     if not row:
#         return {"ok": False, "message": "Product not found", "tenant_id": tenant_id, "product_id": product_id}

#     product = {
#         "id": int(row[0]),
#         "tenant_id": int(row[1]),
#         "slug": row[2],
#         "title": row[3],
#         "description": row[4],
#         "image_url": row[5],
#         "price": str(row[6]) if row[6] is not None else None,
#         "discounted_price": str(row[7]) if row[7] is not None else None,
#         "price_cents": int(row[8]) if row[8] is not None else None,
#         "currency": row[9],
#         "is_published": bool(row[10]),
#         "identifier": row[11],
#         "stock_status": row[12],
#         "created_at": str(row[13]),
#     }

#     if include_courses:
#         _ensure_product_courses_table(db)
#         linked = db.execute(
#             text("""
#                 select pc.moodle_course_id, c.fullname, c.summary
#                   from product_courses pc
#                   left join courses c
#                     on c.tenant_id = pc.tenant_id and c.moodle_course_id = pc.moodle_course_id
#                  where pc.tenant_id = :t and pc.product_id = :p
#                  order by pc.moodle_course_id asc
#             """),
#             {"t": tenant_id, "p": product_id},
#         ).fetchall()

#         product["courses"] = [{
#             "moodle_course_id": int(r[0]),
#             "fullname": r[1],
#             "summary": r[2],
#         } for r in linked]

#     if include_related:
#         _ensure_related_products_table(db)
#         related_rows = db.execute(
#             text("""
#                 select p2.id, p2.slug, p2.title, p2.description, p2.image_url,
#                        p2.price, p2.discounted_price, p2.currency, p2.is_published, p2.stock_status
#                   from product_related pr
#                   join products p2
#                     on p2.id = pr.related_product_id and p2.tenant_id = pr.tenant_id
#                  where pr.tenant_id = :t and pr.product_id = :p
#                  order by pr.created_at desc
#             """),
#             {"t": tenant_id, "p": product_id},
#         ).fetchall()

#         product["related_products"] = [{
#             "id": int(r[0]),
#             "slug": r[1],
#             "title": r[2],
#             "description": r[3],
#             "image_url": r[4],
#             "price": str(r[5]) if r[5] is not None else None,
#             "discounted_price": str(r[6]) if r[6] is not None else None,
#             "currency": r[7],
#             "is_published": bool(r[8]),
#             "stock_status": r[9],
#         } for r in related_rows]

#     if include_categories:
#         _ensure_categories_tables(db)
#         cat_rows = db.execute(
#             text("""
#                 select c.id, c.name, c.slug
#                   from product_categories pc
#                   join categories c
#                     on c.id = pc.category_id and c.tenant_id = pc.tenant_id
#                  where pc.tenant_id = :t and pc.product_id = :p
#                  order by c.name asc
#             """),
#             {"t": tenant_id, "p": product_id},
#         ).fetchall()

#         product["categories"] = [{
#             "id": int(r[0]),
#             "name": r[1],
#             "slug": r[2],
#         } for r in cat_rows]

#     return {"ok": True, "tenant_id": tenant_id, "product": product}


# @router.patch("/products/{product_id}")
# def update_product(
#     product_id: int,
#     payload: UpdateProductPayload,
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     db: Session = Depends(get_db),
# ):
#     _ensure_products_table(db)

#     prod = db.execute(
#         text("select id from products where id=:p and tenant_id=:t"),
#         {"p": product_id, "t": tenant_id},
#     ).fetchone()
#     if not prod:
#         return {"ok": False, "message": "Product not found for this tenant", "tenant_id": tenant_id}

#     updates = {}
#     sets = []

#     # slug is derived from title in create, but you may still want to allow editing slug on update.
#     # If you want to DISALLOW slug edits, delete this block.
#     # (Your current UpdateProductPayload does not include slug, so this is not used.)
#     # if payload.slug is not None:
#     #     updates["slug"] = slugify(payload.slug)
#     #     sets.append("slug = :slug")

#     if payload.title is not None:
#         updates["title"] = payload.title
#         sets.append("title = :title")

#     if payload.description is not None:
#         updates["description"] = payload.description
#         sets.append("description = :description")

#     if payload.image_url is not None:
#         updates["image_url"] = payload.image_url.strip() if payload.image_url else None
#         sets.append("image_url = :image_url")

#     if payload.price is not None:
#         price = Decimal(str(payload.price))
#         if price <= Decimal("0"):
#             return {"ok": False, "message": "price must be > 0"}
#         updates["price"] = str(price)
#         updates["price_cents"] = to_cents(price)
#         sets.append("price = :price")
#         sets.append("price_cents = :price_cents")

#     if "discounted_price" in getattr(payload, "model_fields_set", set()):
#         discounted = _parse_optional_price(payload.discounted_price)
#         current_price = None
#         if payload.price is None:
#             current_price = db.execute(
#                 text("select price from products where tenant_id=:t and id=:p"),
#                 {"t": tenant_id, "p": product_id},
#             ).scalar()
#         base_price = Decimal(str(payload.price)) if payload.price is not None else (
#             Decimal(str(current_price)) if current_price is not None else None
#         )
#         if base_price is not None and discounted is not None and discounted >= base_price:
#             return {"ok": False, "message": "discounted_price must be < price"}
#         updates["discounted_price"] = str(discounted) if discounted is not None else None
#         sets.append("discounted_price = :discounted_price")

#     if payload.currency is not None:
#         updates["currency"] = payload.currency.lower().strip()
#         sets.append("currency = :currency")

#     if payload.is_published is not None:
#         updates["is_published"] = bool(payload.is_published)
#         sets.append("is_published = :is_published")

#     if payload.identifier is not None:
#         updates["identifier"] = payload.identifier.strip() if payload.identifier else None
#         sets.append("identifier = :identifier")

#     if payload.stock_status is not None:
#         stock_status = payload.stock_status.strip().lower()
#         if stock_status not in ALLOWED_STOCK_STATUSES:
#             return {"ok": False, "message": f"stock_status must be one of {sorted(ALLOWED_STOCK_STATUSES)}"}
#         updates["stock_status"] = stock_status
#         sets.append("stock_status = :stock_status")

#     category_ids = _normalize_category_ids(payload.category_ids)
#     course_ids = _normalize_course_ids(payload.course_ids)

#     if not sets and category_ids is None and course_ids is None:
#         return {"ok": False, "message": "No fields to update"}

#     updates["tenant_id"] = tenant_id
#     updates["product_id"] = product_id

#     try:
#         row = None
#         if sets:
#             row = db.execute(
#                 text(f"""
#                     update products
#                        set {", ".join(sets)}, updated_at = now()
#                      where id = :product_id and tenant_id = :tenant_id
#                     returning id, tenant_id, slug, title, description,
#                               image_url, price, discounted_price, price_cents, currency, is_published,
#                               identifier, stock_status, created_at
#                 """),
#                 updates,
#             ).fetchone()

#         if course_ids is not None:
#             _set_product_courses(db, tenant_id, product_id, course_ids)

#         if category_ids is not None:
#             _set_product_categories(db, tenant_id, product_id, category_ids)

#         if row is None:
#             row = db.execute(
#                 text("""
#                     select id, tenant_id, slug, title, description,
#                            image_url, price, discounted_price, price_cents, currency, is_published,
#                            identifier, stock_status, created_at
#                       from products
#                      where tenant_id = :t and id = :p
#                      limit 1
#                 """),
#                 {"t": tenant_id, "p": product_id},
#             ).fetchone()

#         db.commit()

#     except ValueError as ve:
#         db.rollback()
#         return {"ok": False, "message": str(ve)}
#     except Exception as e:
#         db.rollback()
#         return {"ok": False, "message": f"DB error: {type(e).__name__}: {str(e)}"}

#     return {
#         "ok": True,
#         "tenant_id": tenant_id,
#         "product": {
#             "id": int(row[0]),
#             "tenant_id": int(row[1]),
#             "slug": row[2],
#             "title": row[3],
#             "description": row[4],
#             "image_url": row[5],
#             "price": str(row[6]) if row[6] is not None else None,
#             "discounted_price": str(row[7]) if row[7] is not None else None,
#             "price_cents": int(row[8]) if row[8] is not None else None,
#             "currency": row[9],
#             "is_published": bool(row[10]),
#             "identifier": row[11],
#             "stock_status": row[12],
#             "created_at": str(row[13]),
#             "course_ids": course_ids if course_ids is not None else None,
#             "category_ids": category_ids if category_ids is not None else None,
#         },
#     }

# @router.post("/products/{product_id}/image")
# async def upload_product_image_endpoint(
#     product_id: int,
#     image: UploadFile = File(...),
#     tenant_id: int = Depends(get_tenant_id_from_request),
#     db: Session = Depends(get_db),
# ):
#     """
#     Upload image (multipart/form-data) to Supabase Storage and save public URL to products.image_url

#     Frontend:
#       const fd = new FormData();
#       fd.append("image", file);
#       fetch(`/api/products/${productId}/image`, { method: "POST", body: fd });
#     """
#     _ensure_products_table(db)

#     # Validate product belongs to tenant
#     exists = db.execute(
#         text("select id from products where tenant_id = :t and id = :p limit 1"),
#         {"t": tenant_id, "p": product_id},
#     ).fetchone()
#     if not exists:
#         raise HTTPException(status_code=404, detail="Product not found for this tenant")

#     # Read bytes once
#     data = await image.read()
#     _validate_image_bytes(image, data, max_mb=5)

#     key = _make_storage_key(tenant_id, product_id, image.content_type or "")

#     # Upload to Supabase using your helper.
#     # This block supports common helper signatures:
#     # - upload_product_image(data: bytes, path: str, content_type: str) -> str|dict
#     # - upload_product_image(file: UploadFile, path: str) -> str|dict
#     try:
#         public_url = None

#         try:
#             # Most common: bytes + path + content_type
#             res = upload_product_image(data=data, path=key, content_type=image.content_type)
#         except TypeError:
#             # Alternate: UploadFile + path
#             res = upload_product_image(file=image, path=key)

#         # Normalize return
#         if isinstance(res, str):
#             public_url = res
#         elif isinstance(res, dict):
#             public_url = res.get("public_url") or res.get("url") or res.get("publicUrl")
#         else:
#             public_url = None

#         if not public_url:
#             raise HTTPException(
#                 status_code=500,
#                 detail="upload_product_image did not return a public url",
#             )

#         # Save to DB
#         db.execute(
#             text("""
#                 update products
#                    set image_url = :url,
#                        updated_at = now()
#                  where tenant_id = :t and id = :p
#             """),
#             {"url": public_url, "t": tenant_id, "p": product_id},
#         )
#         db.commit()

#     except HTTPException:
#         db.rollback()
#         raise
#     except Exception as e:
#         db.rollback()
#         raise HTTPException(
#             status_code=500,
#             detail={"message": "Failed to upload image", "error": f"{type(e).__name__}: {str(e)}"},
#         )

#     return {
#         "ok": True,
#         "tenant_id": tenant_id,
#         "product_id": product_id,
#         "image_url": public_url,
#         "path": key,
#     }

from decimal import Decimal, ROUND_HALF_UP
import re
import json
from uuid import uuid4
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, Form, UploadFile, File, HTTPException, status
from pydantic import BaseModel, condecimal
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from collections import defaultdict

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request  # ✅ tenant resolver
from app.core.supabase import upload_product_image       # ✅ your helper

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
    return int((price * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _parse_optional_price(value) -> Decimal | None:
    if value is None:
        return None
    d = Decimal(str(value))
    if d <= Decimal("0"):
        return None
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _ensure_products_table(db: Session) -> None:
    """
    Products table (moodle_course_id deprecated - do NOT use it anymore).
    Course linkage is via product_courses.
    """
    db.execute(text("""
        create table if not exists products (
          id bigserial primary key
        );
    """))
    db.commit()

    db.execute(text("alter table products add column if not exists tenant_id bigint;"))
    db.execute(text("alter table products add column if not exists slug text;"))
    db.execute(text("alter table products add column if not exists price_cents int;"))
    db.execute(text("alter table products add column if not exists currency text default 'usd';"))
    db.execute(text("alter table products add column if not exists is_published boolean default false;"))
    db.execute(text("alter table products add column if not exists created_at timestamptz default now();"))
    db.execute(text("alter table products add column if not exists updated_at timestamptz default now();"))
    db.execute(text("alter table products add column if not exists price numeric(10,2);"))
    db.execute(text("alter table products add column if not exists title text;"))
    db.execute(text("alter table products add column if not exists description text;"))
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


def _ensure_courses_table(db: Session) -> None:
    """
    Your project likely already has this (from Moodle sync),
    but we keep this here so this router is self-contained.
    """
    db.execute(text("""
        create table if not exists courses (
          id bigserial primary key,
          tenant_id bigint not null references tenants(id) on delete cascade,
          moodle_course_id bigint not null,
          fullname text not null,
          summary text,
          updated_at timestamptz not null default now(),
          unique (tenant_id, moodle_course_id)
        );
    """))
    db.commit()


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


def _normalize_category_ids(category_ids: list[int] | None) -> list[int] | None:
    if category_ids is None:
        return None
    ids = sorted({int(x) for x in category_ids if int(x) > 0})
    return ids  # [] meaning "clear all"


def _set_product_categories(db: Session, tenant_id: int, product_id: int, category_ids: list[int]) -> None:
    _ensure_categories_tables(db)

    db.execute(
        text("delete from product_categories where tenant_id = :t and product_id = :p"),
        {"t": tenant_id, "p": product_id},
    )

    if not category_ids:
        return

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


def _normalize_course_ids(course_ids: list[int] | None) -> list[int] | None:
    if course_ids is None:
        return None
    ids = sorted({int(x) for x in course_ids if int(x) > 0})
    return ids  # [] meaning "clear all"


def _set_product_courses(db: Session, tenant_id: int, product_id: int, course_ids: list[int]) -> None:
    _ensure_product_courses_table(db)
    _ensure_courses_table(db)

    db.execute(
        text("delete from product_courses where tenant_id = :t and product_id = :p"),
        {"t": tenant_id, "p": product_id},
    )

    if not course_ids:
        return

    existing = db.execute(
        text("""
            select moodle_course_id
              from courses
             where tenant_id = :t
               and moodle_course_id = any(:ids)
        """),
        {"t": tenant_id, "ids": course_ids},
    ).fetchall()
    existing_ids = {int(r[0]) for r in existing}

    missing = [cid for cid in course_ids if cid not in existing_ids]
    if missing:
        raise ValueError(f"Invalid course_ids for tenant {tenant_id}: {missing}")

    db.execute(
        text("""
            insert into product_courses (tenant_id, product_id, moodle_course_id, created_at)
            values (:t, :p, :c, now())
            on conflict (tenant_id, product_id, moodle_course_id) do nothing
        """),
        [{"t": tenant_id, "p": product_id, "c": cid} for cid in course_ids],
    )


# -----------------------------
# Image upload helpers
# -----------------------------
def _ext_from_content_type(content_type: str) -> str:
    ct = (content_type or "").lower()
    if ct == "image/png":
        return ".png"
    if ct in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if ct == "image/webp":
        return ".webp"
    return ""


def _validate_image_bytes(image: UploadFile, data: bytes, max_mb: int = 5) -> None:
    allowed = {"image/png", "image/jpeg", "image/webp"}
    if not image.content_type or image.content_type.lower() not in allowed:
        raise HTTPException(status_code=400, detail="image must be png, jpg, or webp")

    max_bytes = max_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail=f"image too large (max {max_mb}MB)")


def _make_storage_key(tenant_id: int, product_id: int, content_type: str) -> str:
    ext = _ext_from_content_type(content_type) or ".bin"
    return f"tenants/{tenant_id}/products/{product_id}/{uuid4().hex}{ext}"


def _parse_ids_json(name: str, raw: str | None) -> list[int] | None:
    """
    Accepts JSON array passed as a string in multipart form-data:
      course_ids: "[1,2,3]"
      category_ids: "[10,11]"
    Returns:
      None if not provided
      [] if provided as []
    """
    if raw is None:
        return None

    raw = raw.strip()
    if raw == "":
        return None

    try:
        value = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{name} must be a JSON array string like [1,2,3]")

    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail=f"{name} must be a JSON array")

    try:
        ids = sorted({int(x) for x in value if int(x) > 0})
    except Exception:
        raise HTTPException(status_code=400, detail=f"{name} must be an array of integers")

    return ids


def _extract_public_url(res) -> str | None:
    if isinstance(res, str):
        return res
    if isinstance(res, dict):
        return res.get("public_url") or res.get("url") or res.get("publicUrl")
    return None


def _upload_to_supabase(image: UploadFile, data: bytes, key: str) -> str:
    """
    Calls your upload_product_image helper using positional args only.
    Tries common signatures until one works.

    Your error: upload_product_image() got an unexpected keyword argument 'path'
    => so we NEVER pass keywords.
    """
    attempts = [
        lambda: upload_product_image(image, key),                     # (file, path)
        lambda: upload_product_image(image, key, image.content_type), # (file, path, content_type)
        lambda: upload_product_image(data, key),                      # (bytes, path)
        lambda: upload_product_image(data, key, image.content_type),  # (bytes, path, content_type)
    ]

    last_err: Exception | None = None
    for fn in attempts:
        try:
            res = fn()
            url = _extract_public_url(res)
            if not url:
                raise RuntimeError("upload_product_image did not return a public url")
            return url
        except TypeError as e:
            last_err = e
            continue

    raise TypeError(f"upload_product_image signature mismatch. Last error: {last_err}")


# -----------------------------
# Schemas (for JSON PATCH)
# -----------------------------
class UpdateProductPayload(BaseModel):
    title: str | None = None
    description: str | None = None
    image_url: str | None = None

    price: condecimal(max_digits=10, decimal_places=2) | None = None
    discounted_price: condecimal(max_digits=10, decimal_places=2) | None = None

    currency: str | None = None
    is_published: bool | None = None

    identifier: str | None = None
    stock_status: str | None = None

    course_ids: list[int] | None = None   # send [] to clear
    category_ids: list[int] | None = None # send [] to clear


# -----------------------------
# Routes
# -----------------------------

@router.post("/products", status_code=status.HTTP_201_CREATED)
async def create_product(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),

    # required
    title: str = Form(...),
    price: str = Form(...),

    # optional
    description: str | None = Form(None),
    discounted_price: str | None = Form(None),
    currency: str = Form("usd"),
    identifier: str | None = Form(None),
    stock_status: str = Form("available"),

    # arrays as JSON strings
    course_ids: str | None = Form(None),
    category_ids: str | None = Form(None),

    # optional image file
    image: UploadFile | None = File(None),
):
    _ensure_products_table(db)

    title_clean = (title or "").strip()
    if not title_clean:
        raise HTTPException(status_code=400, detail="title is required")

    slug = slugify(title_clean)

    try:
        price_dec = Decimal(str(price))
    except Exception:
        raise HTTPException(status_code=400, detail="price must be a valid number")
    if price_dec <= Decimal("0"):
        raise HTTPException(status_code=400, detail="price must be > 0")

    discounted_dec: Decimal | None = None
    if discounted_price is not None and str(discounted_price).strip() != "":
        discounted_dec = _parse_optional_price(discounted_price)
        if discounted_dec is not None and discounted_dec >= price_dec:
            raise HTTPException(status_code=400, detail="discounted_price must be < price")

    stock_status_clean = (stock_status or "available").strip().lower()
    if stock_status_clean not in ALLOWED_STOCK_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"stock_status must be one of {sorted(ALLOWED_STOCK_STATUSES)}",
        )

    currency_clean = (currency or "usd").lower().strip() or "usd"
    price_cents = to_cents(price_dec)

    parsed_course_ids = _parse_ids_json("course_ids", course_ids)
    parsed_category_ids = _parse_ids_json("category_ids", category_ids)

    image_bytes: bytes | None = None
    if image is not None:
        image_bytes = await image.read()
        _validate_image_bytes(image, image_bytes, max_mb=5)

    try:
        # 1) Insert product first (image_url null initially). is_published always false on create.
        row = db.execute(
            text("""
                insert into products
                  (tenant_id, slug, title, description, image_url,
                   price, discounted_price, price_cents, currency, is_published,
                   identifier, stock_status, updated_at)
                values
                  (:tenant_id, :slug, :title, :description, null,
                   :price, :discounted_price, :price_cents, :currency,
                   false,
                   :identifier, :stock_status, now())
                returning
                  id, tenant_id, slug, title, description,
                  image_url, price, discounted_price, price_cents, currency, is_published,
                  identifier, stock_status, created_at
            """),
            {
                "tenant_id": tenant_id,
                "slug": slug,
                "title": title_clean,
                "description": description if description is not None else None,
                "price": str(price_dec),
                "discounted_price": str(discounted_dec) if discounted_dec is not None else None,
                "price_cents": price_cents,
                "currency": currency_clean,
                "identifier": identifier.strip() if identifier else None,
                "stock_status": stock_status_clean,
            },
        ).fetchone()

        product_id = int(row[0])

        # 2) Relations (replace behavior if provided)
        if parsed_course_ids is not None:
            _set_product_courses(db, tenant_id, product_id, parsed_course_ids)
        if parsed_category_ids is not None:
            _set_product_categories(db, tenant_id, product_id, parsed_category_ids)

        # 3) Upload image (if provided) and save public URL
        if image is not None and image_bytes is not None:
            key = _make_storage_key(tenant_id, product_id, image.content_type or "")
            public_url = _upload_to_supabase(image, image_bytes, key)

            db.execute(
                text("""
                    update products
                       set image_url = :url,
                           updated_at = now()
                     where tenant_id = :t and id = :p
                """),
                {"url": public_url, "t": tenant_id, "p": product_id},
            )

            # rebuild row for response with new image_url
            row = (
                int(row[0]), int(row[1]), row[2], row[3], row[4],
                public_url, row[6], row[7], row[8], row[9], row[10],
                row[11], row[12], row[13]
            )

        db.commit()

    except HTTPException:
        db.rollback()
        raise

    except ValueError as ve:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    except IntegrityError as ie:
        db.rollback()
        msg = str(getattr(ie, "orig", ie))

        # 409 on duplicate slug per tenant
        if "duplicate key value violates unique constraint" in msg and (
            "products_tenant_id_slug_key" in msg or "products_tenant_slug_uniq" in msg
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "A product with this title/slug already exists for this tenant.",
                    "tenant_id": tenant_id,
                    "slug": slug,
                },
            )

        raise HTTPException(status_code=400, detail={"message": "Database integrity error", "error": msg})

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"message": "DB error creating product", "error": f"{type(e).__name__}: {str(e)}"},
        )

    return {
        "ok": True,
        "product": {
            "id": int(row[0]),
            "tenant_id": int(row[1]),
            "slug": row[2],
            "title": row[3],
            "description": row[4],
            "image_url": row[5],
            "price": str(row[6]) if row[6] is not None else None,
            "discounted_price": str(row[7]) if row[7] is not None else None,
            "price_cents": int(row[8]) if row[8] is not None else None,
            "currency": row[9],
            "is_published": bool(row[10]),
            "identifier": row[11],
            "stock_status": row[12],
            "created_at": str(row[13]),
            "course_ids": parsed_course_ids if parsed_course_ids is not None else None,
            "category_ids": parsed_category_ids if parsed_category_ids is not None else None,
        },
    }


@router.get("/products/paged")
def list_products_paged(
    tenant_id: int = Depends(get_tenant_id_from_request),
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
            select id, tenant_id, slug, title, description, image_url,
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
            "slug": r[2],
            "title": r[3],
            "description": r[4],
            "image_url": r[5],
            "price": str(r[6]) if r[6] is not None else None,
            "discounted_price": str(r[7]) if r[7] is not None else None,
            "price_cents": int(r[8]) if r[8] is not None else None,
            "currency": r[9],
            "is_published": bool(r[10]),
            "identifier": r[11],
            "stock_status": r[12],
            "created_at": str(r[13]),
            "categories": [],
        })

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
        "tenant_id": tenant_id,
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "total_pages": int(total_pages),
        "items": items,
    }


@router.get("/products/{product_id}")
def get_product_detail(
    product_id: int,
    tenant_id: int = Depends(get_tenant_id_from_request),
    include_courses: bool = True,
    include_related: bool = True,
    include_categories: bool = True,
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)

    row = db.execute(
        text("""
            select id, tenant_id, slug, title, description, image_url,
                   price, discounted_price, price_cents, currency, is_published,
                   identifier, stock_status, created_at
              from products
             where tenant_id = :t and id = :id
             limit 1
        """),
        {"t": tenant_id, "id": product_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Product not found")

    product = {
        "id": int(row[0]),
        "tenant_id": int(row[1]),
        "slug": row[2],
        "title": row[3],
        "description": row[4],
        "image_url": row[5],
        "price": str(row[6]) if row[6] is not None else None,
        "discounted_price": str(row[7]) if row[7] is not None else None,
        "price_cents": int(row[8]) if row[8] is not None else None,
        "currency": row[9],
        "is_published": bool(row[10]),
        "identifier": row[11],
        "stock_status": row[12],
        "created_at": str(row[13]),
    }

    if include_courses:
        _ensure_product_courses_table(db)
        _ensure_courses_table(db)
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
                select p2.id, p2.slug, p2.title, p2.description, p2.image_url,
                       p2.price, p2.discounted_price, p2.currency, p2.is_published, p2.stock_status
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
            "price": str(r[5]) if r[5] is not None else None,
            "discounted_price": str(r[6]) if r[6] is not None else None,
            "currency": r[7],
            "is_published": bool(r[8]),
            "stock_status": r[9],
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

    return {"ok": True, "tenant_id": tenant_id, "product": product}


@router.patch("/products/{product_id}")
def update_product(
    product_id: int,
    payload: UpdateProductPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)

    prod = db.execute(
        text("select id from products where id=:p and tenant_id=:t"),
        {"p": product_id, "t": tenant_id},
    ).fetchone()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found for this tenant")

    updates = {}
    sets = []

    if payload.title is not None:
        updates["title"] = payload.title.strip()
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
            raise HTTPException(status_code=400, detail="price must be > 0")
        updates["price"] = str(price)
        updates["price_cents"] = to_cents(price)
        sets.append("price = :price")
        sets.append("price_cents = :price_cents")

    if "discounted_price" in getattr(payload, "model_fields_set", set()):
        discounted = _parse_optional_price(payload.discounted_price)

        current_price = None
        if payload.price is None:
            current_price = db.execute(
                text("select price from products where tenant_id=:t and id=:p"),
                {"t": tenant_id, "p": product_id},
            ).scalar()

        base_price = Decimal(str(payload.price)) if payload.price is not None else (
            Decimal(str(current_price)) if current_price is not None else None
        )
        if base_price is not None and discounted is not None and discounted >= base_price:
            raise HTTPException(status_code=400, detail="discounted_price must be < price")

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
            raise HTTPException(status_code=400, detail=f"stock_status must be one of {sorted(ALLOWED_STOCK_STATUSES)}")
        updates["stock_status"] = stock_status
        sets.append("stock_status = :stock_status")

    category_ids = _normalize_category_ids(payload.category_ids)
    course_ids = _normalize_course_ids(payload.course_ids)

    if not sets and category_ids is None and course_ids is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["tenant_id"] = tenant_id
    updates["product_id"] = product_id

    try:
        row = None
        if sets:
            row = db.execute(
                text(f"""
                    update products
                       set {", ".join(sets)}, updated_at = now()
                     where id = :product_id and tenant_id = :tenant_id
                    returning id, tenant_id, slug, title, description,
                              image_url, price, discounted_price, price_cents, currency, is_published,
                              identifier, stock_status, created_at
                """),
                updates,
            ).fetchone()

        if course_ids is not None:
            _set_product_courses(db, tenant_id, product_id, course_ids)

        if category_ids is not None:
            _set_product_categories(db, tenant_id, product_id, category_ids)

        if row is None:
            row = db.execute(
                text("""
                    select id, tenant_id, slug, title, description,
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
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB error: {type(e).__name__}: {str(e)}")

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "product": {
            "id": int(row[0]),
            "tenant_id": int(row[1]),
            "slug": row[2],
            "title": row[3],
            "description": row[4],
            "image_url": row[5],
            "price": str(row[6]) if row[6] is not None else None,
            "discounted_price": str(row[7]) if row[7] is not None else None,
            "price_cents": int(row[8]) if row[8] is not None else None,
            "currency": row[9],
            "is_published": bool(row[10]),
            "identifier": row[11],
            "stock_status": row[12],
            "created_at": str(row[13]),
            "course_ids": course_ids if course_ids is not None else None,
            "category_ids": category_ids if category_ids is not None else None,
        },
    }


@router.post("/products/{product_id}/image")
async def upload_product_image_endpoint(
    product_id: int,
    image: UploadFile = File(...),
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    _ensure_products_table(db)

    exists = db.execute(
        text("select id from products where tenant_id = :t and id = :p limit 1"),
        {"t": tenant_id, "p": product_id},
    ).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Product not found for this tenant")

    data = await image.read()
    _validate_image_bytes(image, data, max_mb=5)

    key = _make_storage_key(tenant_id, product_id, image.content_type or "")

    try:
        public_url = _upload_to_supabase(image, data, key)

        db.execute(
            text("""
                update products
                   set image_url = :url,
                       updated_at = now()
                 where tenant_id = :t and id = :p
            """),
            {"url": public_url, "t": tenant_id, "p": product_id},
        )
        db.commit()

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"message": "Failed to upload image", "error": f"{type(e).__name__}: {str(e)}"},
        )

    return {"ok": True, "tenant_id": tenant_id, "product_id": product_id, "image_url": public_url, "path": key}