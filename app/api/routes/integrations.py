# from fastapi import APIRouter, Depends, Query
# from sqlalchemy.orm import Session
# from sqlalchemy import text
# from pydantic import BaseModel, HttpUrl
# import re

# from app.core.db import get_db
# from app.services.moodle import MoodleClient, MoodleError

# router = APIRouter()

# DEFAULT_TENANT_ID = 1  # single-tenant MVP

# class MoodleConnectPayload(BaseModel):
#     moodle_url: HttpUrl
#     token: str

# def _ensure_tenants_table(db: Session) -> None:
#     db.execute(text("""
#         create table if not exists tenants (
#           id bigserial primary key,
#           name text not null default 'default',
#           moodle_url text,
#           moodle_token text,
#           created_at timestamptz not null default now()
#         );
#     """))
#     db.commit()

# def _ensure_default_tenant(db: Session) -> None:
#     # create tenant id=1 if it doesn't exist
#     exists = db.execute(text("select id from tenants where id = :id"), {"id": DEFAULT_TENANT_ID}).fetchone()
#     if not exists:
#         db.execute(
#             text("insert into tenants (id, name) values (:id, 'default')"),
#             {"id": DEFAULT_TENANT_ID},
#         )
#         db.commit()

# @router.post("/integrations/moodle/connect")
# async def connect_moodle(payload: MoodleConnectPayload, db: Session = Depends(get_db)):
#     """
#     Saves Moodle URL + token, then tests connection.
#     Returns Connected ✅ or error.
#     """
#     _ensure_tenants_table(db)
#     _ensure_default_tenant(db)

#     moodle_url = str(payload.moodle_url).rstrip("/")
#     token = payload.token.strip()

#     # Save config
#     db.execute(
#         text("""
#             update tenants
#                set moodle_url = :moodle_url,
#                    moodle_token = :token
#              where id = :id
#         """),
#         {"moodle_url": moodle_url, "token": token, "id": DEFAULT_TENANT_ID},
#     )
#     db.commit()

#     # Test connection
#     try:
#         client = MoodleClient(moodle_url, token)
#         info = await client.test_connection()

#         return {
#             "connected": True,
#             "message": "Connected ✅",
#             "site_name": info.get("sitename"),
#             "moodle_username": info.get("username"),
#             "moodle_release": info.get("release"),
#             "moodle_version": info.get("version"),
#         }
#     except MoodleError as e:
#         return {"connected": False, "message": f"Connection failed: {str(e)}"}
#     except Exception as e:
#         return {"connected": False, "message": f"Connection failed: {type(e).__name__}: {str(e)}"}

# @router.get("/integrations/moodle/test")
# async def test_moodle(db: Session = Depends(get_db)):
#     """
#     Tests Moodle connection using stored URL/token.
#     """
#     _ensure_tenants_table(db)
#     _ensure_default_tenant(db)

#     row = db.execute(
#         text("select moodle_url, moodle_token from tenants where id = :id"),
#         {"id": DEFAULT_TENANT_ID},
#     ).fetchone()

#     moodle_url = row[0]
#     token = row[1]

#     if not moodle_url or not token:
#         return {"connected": False, "message": "Moodle not configured yet"}

#     try:
#         client = MoodleClient(moodle_url, token)
#         info = await client.test_connection()

#         return {
#             "connected": True,
#             "message": "Connected ✅",
#             "site_name": info.get("sitename"),
#             "moodle_username": info.get("username"),
#             "moodle_release": info.get("release"),
#             "moodle_version": info.get("version"),
#         }
#     except MoodleError as e:
#         return {"connected": False, "message": f"Connection failed: {str(e)}"}
#     except Exception as e:
#         return {"connected": False, "message": f"Connection failed: {type(e).__name__}: {str(e)}"}



# def _ensure_courses_table(db: Session) -> None:
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

# @router.post("/integrations/{tenant_id}/sync-courses")
# async def sync_courses(tenant_id: int, db: Session = Depends(get_db)):
#     """
#     AC:
#     - Clicking “Sync now” pulls courses and stores them for a tenant
#     - Uses core_course_get_courses
#     - Upsert (tenant_id, moodle_course_id)
#     - Stores: id, name(fullname), summary
#     """
#     _ensure_courses_table(db)

#     # 1) Load tenant Moodle config
#     row = db.execute(
#         text("select moodle_url, moodle_token from tenants where id = :id"),
#         {"id": tenant_id},
#     ).fetchone()

#     if not row or not row[0] or not row[1]:
#         return {"ok": False, "message": f"Tenant {tenant_id} not found or Moodle not configured"}

#     moodle_url, moodle_token = row[0], row[1]

#     # 2) Fetch courses from Moodle
#     try:
#         moodle = MoodleClient(moodle_url, moodle_token)
#         courses = await moodle.call("core_course_get_courses")
#         print("Courses", courses)
#     except MoodleError as e:
#         return {"ok": False, "message": f"Moodle error: {str(e)}"}
#     except Exception as e:
#         return {"ok": False, "message": f"Failed to fetch courses: {type(e).__name__}: {str(e)}"}

#     if not isinstance(courses, list):
#         return {"ok": False, "message": "Unexpected response from Moodle (courses not a list)"}

#     # 3) Prepare upsert rows
#     rows = []
#     for c in courses:
#         # Moodle uses fullname; summary can be missing or HTML
#         moodle_course_id = c.get("id")
#         fullname = c.get("fullname") or ""
#         summary = c.get("summary") or ""

#         if not moodle_course_id or not fullname:
#             continue

#         rows.append({
#             "tenant_id": tenant_id,
#             "moodle_course_id": int(moodle_course_id),
#             "fullname": fullname,
#             "summary": summary,
#         })

#     # 4) Upsert into DB
#     upsert_sql = text("""
#         insert into courses (tenant_id, moodle_course_id, fullname, summary, updated_at)
#         values (:tenant_id, :moodle_course_id, :fullname, :summary, now())
#         on conflict (tenant_id, moodle_course_id)
#         do update set
#           fullname = excluded.fullname,
#           summary = excluded.summary,
#           updated_at = now();
#     """)

#     if rows:
#         db.execute(upsert_sql, rows)  # executemany
#         db.commit()

#     return {
#         "ok": True,
#         "tenant_id": tenant_id,
#         "fetched_from_moodle": len(courses),
#         "upserted": len(rows),
#         "message": "Sync complete ✅",
#     }


# @router.get("/integrations/{tenant_id}/moodle/users/exists")
# async def moodle_user_exists(
#     tenant_id: int,
#     email: str = Query(..., min_length=3, description="User email to search in Moodle"),
#     db: Session = Depends(get_db),
# ):
#     """
#     AC:
#     - Given email, detect if user exists in Moodle (for a tenant)
#     - Implement core_user_get_users by email
#     - Return moodle_user_id if found
#     """
#     # 1) Load tenant moodle config
#     row = db.execute(
#         text("select moodle_url, moodle_token from tenants where id = :id"),
#         {"id": tenant_id},
#     ).fetchone()

#     if not row or not row[0] or not row[1]:
#         return {
#             "ok": False,
#             "message": "Tenant not found or Moodle not configured",
#             "tenant_id": tenant_id,
#         }

#     moodle_url, moodle_token = row[0], row[1]

#     # 2) Call Moodle core_user_get_users by email
#     try:
#         moodle = MoodleClient(moodle_url, moodle_token)

#         data = await moodle.call(
#             "core_user_get_users",
#             **{
#                 "criteria[0][key]": "email",
#                 "criteria[0][value]": email.strip(),
#             },
#         )

#         users = data.get("users", []) if isinstance(data, dict) else []
#         if not users:
#             return {
#                 "ok": True,
#                 "exists": False,
#                 "email": email,
#                 "moodle_user_id": None,
#             }

#         # Moodle returns array; take the first match
#         moodle_user_id = users[0].get("id")

#         return {
#             "ok": True,
#             "exists": True,
#             "email": email,
#             "moodle_user_id": int(moodle_user_id) if moodle_user_id is not None else None,
#         }

#     except MoodleError as e:
#         return {"ok": False, "message": f"Moodle error: {str(e)}"}
#     except Exception as e:
#         return {"ok": False, "message": f"Failed: {type(e).__name__}: {str(e)}"}

# _cat_slug_re = re.compile(r"[^a-z0-9-]+")

# def category_slugify(value: str) -> str:
#     value = (value or "").strip().lower()
#     value = value.replace("_", "-").replace(" ", "-")
#     value = _cat_slug_re.sub("", value)
#     value = re.sub(r"-{2,}", "-", value).strip("-")
#     return value or "category"

# def _ensure_categories_table(db: Session) -> None:
#     db.execute(text("""
#         create table if not exists categories (
#           id bigserial primary key,
#           tenant_id bigint not null references tenants(id) on delete cascade,
#           name text not null,
#           slug text not null,
#           moodle_category_id bigint,
#           created_at timestamptz not null default now(),
#           unique (tenant_id, slug)
#         );
#     """))
#     db.commit()

#     # Ensure column exists even if table already existed
#     db.execute(text("alter table categories add column if not exists moodle_category_id bigint;"))
#     db.commit()


# @router.post("/integrations/{tenant_id}/sync-categories")
# async def sync_categories(tenant_id: int, db: Session = Depends(get_db)):
#     _ensure_tenants_table(db)
#     _ensure_default_tenant(db)
#     _ensure_categories_table(db)

#     # 1) Load tenant Moodle config
#     row = db.execute(
#         text("select moodle_url, moodle_token from tenants where id = :id"),
#         {"id": tenant_id},
#     ).fetchone()

#     if not row or not row[0] or not row[1]:
#         return {"ok": False, "message": f"Tenant {tenant_id} not found or Moodle not configured"}

#     moodle_url, moodle_token = row[0], row[1]

#     # 2) Fetch categories from Moodle
#     try:
#         moodle = MoodleClient(moodle_url, moodle_token)
#         cats = await moodle.call("core_course_get_categories")
#     except MoodleError as e:
#         return {"ok": False, "message": f"Moodle error: {str(e)}"}
#     except Exception as e:
#         return {"ok": False, "message": f"Failed to fetch categories: {type(e).__name__}: {str(e)}"}

#     if not isinstance(cats, list):
#         return {"ok": False, "message": "Unexpected response from Moodle (categories not a list)"}

#     # 3) Prepare rows
#     rows = []
#     for c in cats:
#         moodle_category_id = c.get("id")
#         name = (c.get("name") or "").strip()
#         if not moodle_category_id or not name:
#             continue

#         rows.append({
#             "tenant_id": tenant_id,
#             "moodle_category_id": int(moodle_category_id),
#             "name": name,
#             "slug": category_slugify(name),
#         })
#     print("Rows", rows)
#     # 4) Upsert by moodle_category_id (true key)
#     upsert_sql = text("""
#         insert into categories (tenant_id, moodle_category_id, name, slug, created_at)
#         values (:tenant_id, :moodle_category_id, :name, :slug, now())
#         on conflict (tenant_id, moodle_category_id)
#         do update set
#           name = excluded.name,
#           slug = excluded.slug;
#     """)

#     if rows:
#         db.execute(upsert_sql, rows)
#         db.commit()

#     return {
#         "ok": True,
#         "tenant_id": tenant_id,
#         "fetched_from_moodle": len(cats),
#         "upserted": len(rows),
#         "message": "Category sync complete ✅",
#     }

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, HttpUrl
import re

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request  # <- your new tenant methodology
from app.services.moodle import MoodleClient, MoodleError

router = APIRouter()


class MoodleConnectPayload(BaseModel):
    moodle_url: HttpUrl
    token: str


# -----------------------------
# DB helpers
# -----------------------------
def _ensure_tenants_table(db: Session) -> None:
    """
    Safe schema bootstrap (won't break if table already exists).
    Your real tenants table may have more columns (Stripe, etc). That's fine.
    """
    db.execute(text("""
        create table if not exists tenants (
          id bigserial primary key
        );
    """))
    db.commit()

    # Ensure fields we need exist
    db.execute(text("alter table tenants add column if not exists name text not null default 'default';"))
    db.execute(text("alter table tenants add column if not exists moodle_url text;"))
    db.execute(text("alter table tenants add column if not exists moodle_token text;"))
    db.execute(text("alter table tenants add column if not exists created_at timestamptz not null default now();"))
    db.commit()


def _ensure_tenant_row(db: Session, tenant_id: int) -> None:
    """
    Ensures a tenant row exists for the inferred tenant_id.
    This prevents 'update 0 rows' problems when connecting Moodle for a new tenant.
    """
    _ensure_tenants_table(db)

    exists = db.execute(
        text("select id from tenants where id = :id"),
        {"id": tenant_id},
    ).fetchone()

    if not exists:
        db.execute(
            text("insert into tenants (id, name) values (:id, :name)"),
            {"id": tenant_id, "name": f"tenant-{tenant_id}"},
        )
        db.commit()


def _ensure_courses_table(db: Session) -> None:
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


_cat_slug_re = re.compile(r"[^a-z0-9-]+")


def category_slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("_", "-").replace(" ", "-")
    value = _cat_slug_re.sub("", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "category"


def _ensure_categories_table(db: Session) -> None:
    db.execute(text("""
        create table if not exists categories (
          id bigserial primary key,
          tenant_id bigint not null references tenants(id) on delete cascade,
          name text not null,
          slug text not null,
          moodle_category_id bigint,
          created_at timestamptz not null default now(),
          unique (tenant_id, slug)
        );
    """))
    db.commit()

    # Ensure column exists even if table already existed
    db.execute(text("alter table categories add column if not exists moodle_category_id bigint;"))
    db.commit()

    # REQUIRED for your ON CONFLICT (tenant_id, moodle_category_id)
    db.execute(text("""
        do $$
        begin
          if not exists (
            select 1
              from pg_constraint
             where conname = 'categories_tenant_moodle_category_uniq'
          ) then
            alter table categories
            add constraint categories_tenant_moodle_category_uniq unique (tenant_id, moodle_category_id);
          end if;
        end $$;
    """))
    db.commit()


def _get_tenant_moodle(db: Session, tenant_id: int):
    row = db.execute(
        text("select moodle_url, moodle_token from tenants where id = :id"),
        {"id": tenant_id},
    ).fetchone()

    if not row or not row[0] or not row[1]:
        return None

    return str(row[0]).rstrip("/"), str(row[1]).strip()


# -----------------------------
# Endpoints (tenant inferred from host)
# -----------------------------
@router.post("/integrations/moodle/connect")
async def connect_moodle(
    payload: MoodleConnectPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    """
    Saves Moodle URL + token for the inferred tenant, then tests connection.
    """
    _ensure_tenant_row(db, tenant_id)

    moodle_url = str(payload.moodle_url).rstrip("/")
    token = payload.token.strip()

    db.execute(
        text("""
            update tenants
               set moodle_url = :moodle_url,
                   moodle_token = :token
             where id = :id
        """),
        {"moodle_url": moodle_url, "token": token, "id": tenant_id},
    )
    db.commit()

    # Test connection
    try:
        client = MoodleClient(moodle_url, token)
        info = await client.test_connection()

        return {
            "connected": True,
            "message": "Connected ✅",
            "tenant_id": tenant_id,
            "site_name": info.get("sitename"),
            "moodle_username": info.get("username"),
            "moodle_release": info.get("release"),
            "moodle_version": info.get("version"),
        }
    except MoodleError as e:
        return {"connected": False, "tenant_id": tenant_id, "message": f"Connection failed: {str(e)}"}
    except Exception as e:
        return {"connected": False, "tenant_id": tenant_id, "message": f"Connection failed: {type(e).__name__}: {str(e)}"}


@router.get("/integrations/moodle/test")
async def test_moodle(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    """
    Tests Moodle connection using stored URL/token for the inferred tenant.
    """
    _ensure_tenants_table(db)

    row = db.execute(
        text("select moodle_url, moodle_token from tenants where id = :id"),
        {"id": tenant_id},
    ).fetchone()

    if not row:
        return {"connected": False, "tenant_id": tenant_id, "message": "Tenant not found"}

    moodle_url = row[0]
    token = row[1]

    if not moodle_url or not token:
        return {"connected": False, "tenant_id": tenant_id, "message": "Moodle not configured yet"}

    try:
        client = MoodleClient(str(moodle_url).rstrip("/"), str(token).strip())
        info = await client.test_connection()

        return {
            "connected": True,
            "message": "Connected ✅",
            "tenant_id": tenant_id,
            "site_name": info.get("sitename"),
            "moodle_username": info.get("username"),
            "moodle_release": info.get("release"),
            "moodle_version": info.get("version"),
        }
    except MoodleError as e:
        return {"connected": False, "tenant_id": tenant_id, "message": f"Connection failed: {str(e)}"}
    except Exception as e:
        return {"connected": False, "tenant_id": tenant_id, "message": f"Connection failed: {type(e).__name__}: {str(e)}"}


@router.post("/integrations/moodle/sync-courses")
async def sync_courses(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    """
    Pulls courses from Moodle for the inferred tenant and upserts into local DB.
    Uses: core_course_get_courses
    """
    _ensure_courses_table(db)

    tenant_conf = _get_tenant_moodle(db, tenant_id)
    if not tenant_conf:
        return {"ok": False, "tenant_id": tenant_id, "message": "Tenant not found or Moodle not configured"}

    moodle_url, moodle_token = tenant_conf

    try:
        moodle = MoodleClient(moodle_url, moodle_token)
        courses = await moodle.call("core_course_get_courses")
    except MoodleError as e:
        return {"ok": False, "tenant_id": tenant_id, "message": f"Moodle error: {str(e)}"}
    except Exception as e:
        return {"ok": False, "tenant_id": tenant_id, "message": f"Failed to fetch courses: {type(e).__name__}: {str(e)}"}

    if not isinstance(courses, list):
        return {"ok": False, "tenant_id": tenant_id, "message": "Unexpected response from Moodle (courses not a list)"}

    rows = []
    for c in courses:
        moodle_course_id = c.get("id")
        fullname = (c.get("fullname") or "").strip()
        summary = c.get("summary") or ""

        if not moodle_course_id or not fullname:
            continue

        rows.append({
            "tenant_id": tenant_id,
            "moodle_course_id": int(moodle_course_id),
            "fullname": fullname,
            "summary": summary,
        })

    upsert_sql = text("""
        insert into courses (tenant_id, moodle_course_id, fullname, summary, updated_at)
        values (:tenant_id, :moodle_course_id, :fullname, :summary, now())
        on conflict (tenant_id, moodle_course_id)
        do update set
          fullname = excluded.fullname,
          summary = excluded.summary,
          updated_at = now();
    """)

    if rows:
        db.execute(upsert_sql, rows)
        db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "fetched_from_moodle": len(courses),
        "upserted": len(rows),
        "message": "Sync complete ✅",
    }


@router.get("/integrations/moodle/users/exists")
async def moodle_user_exists(
    email: str = Query(..., min_length=3, description="User email to search in Moodle"),
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    """
    Given email, detect if user exists in Moodle (for inferred tenant).
    Uses: core_user_get_users
    """
    tenant_conf = _get_tenant_moodle(db, tenant_id)
    if not tenant_conf:
        return {"ok": False, "tenant_id": tenant_id, "message": "Tenant not found or Moodle not configured"}

    moodle_url, moodle_token = tenant_conf

    try:
        moodle = MoodleClient(moodle_url, moodle_token)
        data = await moodle.call(
            "core_user_get_users",
            **{
                "criteria[0][key]": "email",
                "criteria[0][value]": email.strip(),
            },
        )

        users = data.get("users", []) if isinstance(data, dict) else []
        if not users:
            return {"ok": True, "tenant_id": tenant_id, "exists": False, "email": email, "moodle_user_id": None}

        moodle_user_id = users[0].get("id")
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "exists": True,
            "email": email,
            "moodle_user_id": int(moodle_user_id) if moodle_user_id is not None else None,
        }

    except MoodleError as e:
        return {"ok": False, "tenant_id": tenant_id, "message": f"Moodle error: {str(e)}"}
    except Exception as e:
        return {"ok": False, "tenant_id": tenant_id, "message": f"Failed: {type(e).__name__}: {str(e)}"}


@router.post("/integrations/moodle/sync-categories")
async def sync_categories(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    """
    Pulls categories from Moodle for inferred tenant and upserts into local DB.
    Uses: core_course_get_categories
    """
    _ensure_tenants_table(db)
    _ensure_categories_table(db)

    tenant_conf = _get_tenant_moodle(db, tenant_id)
    if not tenant_conf:
        return {"ok": False, "tenant_id": tenant_id, "message": "Tenant not found or Moodle not configured"}

    moodle_url, moodle_token = tenant_conf

    try:
        moodle = MoodleClient(moodle_url, moodle_token)
        cats = await moodle.call("core_course_get_categories")
    except MoodleError as e:
        return {"ok": False, "tenant_id": tenant_id, "message": f"Moodle error: {str(e)}"}
    except Exception as e:
        return {"ok": False, "tenant_id": tenant_id, "message": f"Failed to fetch categories: {type(e).__name__}: {str(e)}"}

    if not isinstance(cats, list):
        return {"ok": False, "tenant_id": tenant_id, "message": "Unexpected response from Moodle (categories not a list)"}

    rows = []
    for c in cats:
        moodle_category_id = c.get("id")
        name = (c.get("name") or "").strip()
        if not moodle_category_id or not name:
            continue

        rows.append({
            "tenant_id": tenant_id,
            "moodle_category_id": int(moodle_category_id),
            "name": name,
            "slug": category_slugify(name),
        })

    upsert_sql = text("""
        insert into categories (tenant_id, moodle_category_id, name, slug, created_at)
        values (:tenant_id, :moodle_category_id, :name, :slug, now())
        on conflict (tenant_id, moodle_category_id)
        do update set
          name = excluded.name,
          slug = excluded.slug;
    """)

    if rows:
        db.execute(upsert_sql, rows)
        db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "fetched_from_moodle": len(cats),
        "upserted": len(rows),
        "message": "Category sync complete ✅",
    }