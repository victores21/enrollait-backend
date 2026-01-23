from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import json
import re
from uuid import uuid4
from collections import defaultdict

from fastapi import APIRouter, Depends, Query, Form, UploadFile, File, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request
from app.core.supabase import upload_product_image


router = APIRouter()

_slug_re = re.compile(r"[^a-z0-9-]+")
ALLOWED_STOCK_STATUSES = {"available", "not_available"}


# -----------------------------
# Small helpers
# -----------------------------
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


def _parse_optional_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    v = str(raw).strip().lower()
    if v in ("true", "1", "yes", "y", "on"):
        return True
    if v in ("false", "0", "no", "n", "off"):
        return False
    raise HTTPException(status_code=400, detail="is_published must be a boolean (true/false)")


def _row_price_to_decimal(row_price, row_price_cents) -> Decimal:
    if row_price is not None:
        try:
            return Decimal(str(row_price))
        except Exception:
            pass
    try:
        cents = int(row_price_cents or 0)
        return (Decimal(cents) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _parse_ids_json(name: str, raw: str | None) -> list[int] | None:
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


def _extract_public_url(res) -> str | None:
    if isinstance(res, str):
        return res
    if isinstance(res, dict):
        return res.get("public_url") or res.get("url") or res.get("publicUrl")
    return None


def _upload_to_supabase(image: UploadFile, data: bytes, key: str) -> str:
    """
    Keep compatibility with multiple helper signatures (same as your original).
    NOTE: This is still a network call and can be slow depending on latency.
    """
    attempts = [
        lambda: upload_product_image(image, key),
        lambda: upload_product_image(image, key, image.content_type),
        lambda: upload_product_image(data, key),
        lambda: upload_product_image(data, key, image.content_type),
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
# Relation setters (optimized)
# -----------------------------
def _validate_ids_exist(db: Session, tenant_id: int, table: str, ids: list[int]) -> None:
    if not ids:
        return
    rows = db.execute(
        text(f"select id from {table} where tenant_id = :t and id = any(:ids)"),
        {"t": tenant_id, "ids": ids},
    ).fetchall()
    existing = {int(r[0]) for r in rows}
    missing = [x for x in ids if x not in existing]
    if missing:
        raise ValueError(f"Invalid {table} ids for tenant {tenant_id}: {missing}")


def _set_product_courses(db: Session, tenant_id: int, product_id: int, course_ids: list[int]) -> None:
    db.execute(
        text("delete from product_courses where tenant_id = :t and product_id = :p"),
        {"t": tenant_id, "p": product_id},
    )

    if not course_ids:
        return

    _validate_ids_exist(db, tenant_id, "courses", course_ids)

    # Bulk insert using unnest (fewer params, fewer round trips)
    db.execute(
        text("""
            insert into product_courses (tenant_id, product_id, course_id)
            select :t, :p, x
              from unnest(:ids::bigint[]) as x
            on conflict (tenant_id, product_id, course_id) do nothing
        """),
        {"t": tenant_id, "p": product_id, "ids": course_ids},
    )


def _set_product_categories(db: Session, tenant_id: int, product_id: int, category_ids: list[int]) -> None:
    db.execute(
        text("delete from product_categories where tenant_id = :t and product_id = :p"),
        {"t": tenant_id, "p": product_id},
    )

    if not category_ids:
        return

    _validate_ids_exist(db, tenant_id, "categories", category_ids)

    db.execute(
        text("""
            insert into product_categories (tenant_id, product_id, category_id, created_at)
            select :t, :p, x, now()
              from unnest(:ids::bigint[]) as x
            on conflict (tenant_id, product_id, category_id) do nothing
        """),
        {"t": tenant_id, "p": product_id, "ids": category_ids},
    )


def _set_related_products(db: Session, tenant_id: int, product_id: int, related_product_ids: list[int]) -> None:
    db.execute(
        text("delete from product_related where tenant_id = :t and product_id = :p"),
        {"t": tenant_id, "p": product_id},
    )

    if not related_product_ids:
        return

    if product_id in related_product_ids:
        raise ValueError("related_product_ids cannot include the same product_id")

    _validate_ids_exist(db, tenant_id, "products", related_product_ids)

    db.execute(
        text("""
            insert into product_related (tenant_id, product_id, related_product_id, created_at)
            select :t, :p, x, now()
              from unnest(:ids::bigint[]) as x
            on conflict (tenant_id, product_id, related_product_id) do nothing
        """),
        {"t": tenant_id, "p": product_id, "ids": related_product_ids},
    )


# -----------------------------
# Routes
# -----------------------------
@router.post("/products", status_code=status.HTTP_201_CREATED)
def create_product(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),

    title: str = Form(...),
    price: str = Form(...),

    description: str | None = Form(None),
    discounted_price: str | None = Form(None),
    currency: str = Form("usd"),
    identifier: str | None = Form(None),
    stock_status: str = Form("available"),

    course_ids: str | None = Form(None),     # courses.id list JSON string
    category_ids: str | None = Form(None),   # categories.id list JSON string

    image: UploadFile | None = File(None),
):
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
        raise HTTPException(status_code=400, detail=f"stock_status must be one of {sorted(ALLOWED_STOCK_STATUSES)}")

    currency_clean = (currency or "usd").lower().strip() or "usd"
    price_cents = to_cents(price_dec)

    parsed_course_ids = _parse_ids_json("course_ids", course_ids)
    parsed_category_ids = _parse_ids_json("category_ids", category_ids)

    image_bytes: bytes | None = None
    if image is not None:
        image_bytes = image.file.read()
        _validate_image_bytes(image, image_bytes, max_mb=5)

    try:
        with db.begin():
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

            if parsed_course_ids is not None:
                _set_product_courses(db, tenant_id, product_id, parsed_course_ids)
            if parsed_category_ids is not None:
                _set_product_categories(db, tenant_id, product_id, parsed_category_ids)

            image_url = None
            if image is not None and image_bytes is not None:
                key = _make_storage_key(tenant_id, product_id, image.content_type or "")
                public_url = _upload_to_supabase(image, image_bytes, key)

                image_url = public_url
                db.execute(
                    text("""
                        update products
                           set image_url = :url,
                               updated_at = now()
                         where tenant_id = :t and id = :p
                    """),
                    {"url": public_url, "t": tenant_id, "p": product_id},
                )

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    except IntegrityError as ie:
        msg = str(getattr(ie, "orig", ie))
        if "duplicate key value violates unique constraint" in msg and (
            "products_tenant_id_slug_key" in msg or "products_tenant_slug_uniq" in msg
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "A product with this title/slug already exists for this tenant.", "tenant_id": tenant_id, "slug": slug},
            )
        raise HTTPException(status_code=400, detail={"message": "Database integrity error", "error": msg})

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": "DB error creating product", "error": f"{type(e).__name__}: {str(e)}"},
        )

    # NOTE: row[5] was null on insert; if we uploaded, image_url has the new value
    return {
        "ok": True,
        "product": {
            "id": int(row[0]),
            "tenant_id": int(row[1]),
            "slug": row[2],
            "title": row[3],
            "description": row[4],
            "image_url": image_url if image is not None else row[5],
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
    offset = (page - 1) * page_size

    where = ["tenant_id = :t"]
    params = {"t": tenant_id, "limit": page_size, "offset": offset}

    if published_only:
        where.append("is_published = true")

    if search and search.strip():
        params["q"] = f"%{search.strip().lower()}%"
        where.append("(lower(slug) like :q or lower(coalesce(title,'')) like :q)")

    where_sql = " and ".join(where)

    # Single query: rows + total via window function
    rows = db.execute(
        text(f"""
            select
                id, tenant_id, slug, title, description, image_url,
                price, discounted_price, price_cents, currency, is_published,
                identifier, stock_status, created_at,
                count(*) over() as total_count
              from products
             where {where_sql}
             order by created_at desc
             limit :limit offset :offset
        """),
        params,
    ).fetchall()

    total = int(rows[0][14]) if rows else 0
    total_pages = (total + page_size - 1) // page_size if page_size else 0

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

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "page": page,
        "page_size": page_size,
        "total": total,
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
        linked = db.execute(
            text("""
                select c.id, c.moodle_course_id, c.fullname, c.summary
                  from product_courses pc
                  join courses c
                    on c.id = pc.course_id
                   and c.tenant_id = pc.tenant_id
                 where pc.tenant_id = :t and pc.product_id = :p
                 order by c.fullname asc
            """),
            {"t": tenant_id, "p": product_id},
        ).fetchall()

        product["courses"] = [{
            "course_id": int(r[0]),
            "moodle_course_id": int(r[1]),
            "fullname": r[2],
            "summary": r[3],
        } for r in linked]

    if include_related:
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


@router.post("/products/{product_id}/image")
def upload_product_image_endpoint(
    product_id: int,
    image: UploadFile = File(...),
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    exists = db.execute(
        text("select id from products where tenant_id = :t and id = :p limit 1"),
        {"t": tenant_id, "p": product_id},
    ).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Product not found for this tenant")

    data = image.file.read()
    _validate_image_bytes(image, data, max_mb=5)

    key = _make_storage_key(tenant_id, product_id, image.content_type or "")

    try:
        with db.begin():
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
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": "Failed to upload image", "error": f"{type(e).__name__}: {str(e)}"},
        )

    return {"ok": True, "tenant_id": tenant_id, "product_id": product_id, "image_url": public_url, "path": key}


@router.patch("/products/{product_id}")
def update_product(
    product_id: int,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),

    title: str | None = Form(None),
    description: str | None = Form(None),

    price: str | None = Form(None),
    discounted_price: str | None = Form(None),

    currency: str | None = Form(None),
    identifier: str | None = Form(None),
    stock_status: str | None = Form(None),

    is_published: str | None = Form(None),

    course_ids: str | None = Form(None),
    category_ids: str | None = Form(None),
    related_product_ids: str | None = Form(None),

    image: UploadFile | None = File(None),
):
    current = db.execute(
        text("""
            select id, tenant_id, slug, title, description, image_url,
                   price, discounted_price, price_cents, currency, is_published,
                   identifier, stock_status, created_at
              from products
             where tenant_id = :t and id = :p
             limit 1
        """),
        {"t": tenant_id, "p": product_id},
    ).fetchone()

    if not current:
        raise HTTPException(status_code=404, detail="Product not found")

    current_price_dec = _row_price_to_decimal(current[6], current[8])

    updates: dict[str, object] = {}

    if title is not None:
        title_clean = (title or "").strip()
        if not title_clean:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        updates["title"] = title_clean
        updates["slug"] = slugify(title_clean)

    if description is not None:
        desc_clean = (description or "").strip()
        updates["description"] = desc_clean if desc_clean != "" else None

    new_price_dec: Decimal | None = None
    if price is not None:
        if str(price).strip() == "":
            raise HTTPException(status_code=400, detail="price cannot be empty")
        try:
            new_price_dec = Decimal(str(price))
        except Exception:
            raise HTTPException(status_code=400, detail="price must be a valid number")
        if new_price_dec <= Decimal("0"):
            raise HTTPException(status_code=400, detail="price must be > 0")
        updates["price"] = str(new_price_dec)
        updates["price_cents"] = to_cents(new_price_dec)

    if discounted_price is not None:
        if str(discounted_price).strip() == "":
            updates["discounted_price"] = None
        else:
            discounted_dec = _parse_optional_price(discounted_price)
            if discounted_dec is None:
                updates["discounted_price"] = None
            else:
                price_ref = new_price_dec if new_price_dec is not None else current_price_dec
                if discounted_dec >= price_ref:
                    raise HTTPException(status_code=400, detail="discounted_price must be < price")
                updates["discounted_price"] = str(discounted_dec)

    if currency is not None:
        currency_clean = (currency or "").strip().lower()
        if not currency_clean:
            raise HTTPException(status_code=400, detail="currency cannot be empty")
        updates["currency"] = currency_clean

    if identifier is not None:
        ident_clean = (identifier or "").strip()
        updates["identifier"] = ident_clean if ident_clean != "" else None

    if stock_status is not None:
        stock_clean = (stock_status or "").strip().lower()
        if stock_clean not in ALLOWED_STOCK_STATUSES:
            raise HTTPException(status_code=400, detail=f"stock_status must be one of {sorted(ALLOWED_STOCK_STATUSES)}")
        updates["stock_status"] = stock_clean

    pub_val = _parse_optional_bool(is_published)
    if pub_val is not None:
        updates["is_published"] = pub_val

    parsed_course_ids = _parse_ids_json("course_ids", course_ids)
    parsed_category_ids = _parse_ids_json("category_ids", category_ids)
    parsed_related_ids = _parse_ids_json("related_product_ids", related_product_ids)

    image_bytes: bytes | None = None
    if image is not None:
        image_bytes = image.file.read()
        _validate_image_bytes(image, image_bytes, max_mb=5)

    try:
        with db.begin():
            if updates:
                set_parts = [f"{col} = :{col}" for col in updates.keys()]
                set_parts.append("updated_at = now()")
                set_sql = ", ".join(set_parts)

                row = db.execute(
                    text(f"""
                        update products
                           set {set_sql}
                         where tenant_id = :t and id = :p
                         returning
                           id, tenant_id, slug, title, description, image_url,
                           price, discounted_price, price_cents, currency, is_published,
                           identifier, stock_status, created_at
                    """),
                    {**updates, "t": tenant_id, "p": product_id},
                ).fetchone()
            else:
                row = current

            if parsed_course_ids is not None:
                _set_product_courses(db, tenant_id, product_id, parsed_course_ids)

            if parsed_category_ids is not None:
                _set_product_categories(db, tenant_id, product_id, parsed_category_ids)

            if parsed_related_ids is not None:
                _set_related_products(db, tenant_id, product_id, parsed_related_ids)

            if image is not None and image_bytes is not None:
                key = _make_storage_key(tenant_id, product_id, image.content_type or "")
                public_url = _upload_to_supabase(image, image_bytes, key)

                row = db.execute(
                    text("""
                        update products
                           set image_url = :url,
                               updated_at = now()
                         where tenant_id = :t and id = :p
                         returning
                           id, tenant_id, slug, title, description, image_url,
                           price, discounted_price, price_cents, currency, is_published,
                           identifier, stock_status, created_at
                    """),
                    {"url": public_url, "t": tenant_id, "p": product_id},
                ).fetchone()

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    except IntegrityError as ie:
        msg = str(getattr(ie, "orig", ie))
        if "duplicate key value violates unique constraint" in msg and (
            "products_tenant_id_slug_key" in msg or "products_tenant_slug_uniq" in msg
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "A product with this title/slug already exists for this tenant.", "tenant_id": tenant_id, "slug": updates.get("slug") or current[2]},
            )
        raise HTTPException(status_code=400, detail={"message": "Database integrity error", "error": msg})

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": "DB error updating product", "error": f"{type(e).__name__}: {str(e)}"},
        )

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
            "course_ids": parsed_course_ids if parsed_course_ids is not None else None,
            "category_ids": parsed_category_ids if parsed_category_ids is not None else None,
            "related_product_ids": parsed_related_ids if parsed_related_ids is not None else None,
        },
    }