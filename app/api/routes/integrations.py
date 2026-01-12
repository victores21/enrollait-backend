from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, HttpUrl

from app.core.db import get_db
from app.services.moodle import MoodleClient, MoodleError

router = APIRouter()

DEFAULT_TENANT_ID = 1  # single-tenant MVP

class MoodleConnectPayload(BaseModel):
    moodle_url: HttpUrl
    token: str

def _ensure_tenants_table(db: Session) -> None:
    db.execute(text("""
        create table if not exists tenants (
          id bigserial primary key,
          name text not null default 'default',
          moodle_url text,
          moodle_token text,
          created_at timestamptz not null default now()
        );
    """))
    db.commit()

def _ensure_default_tenant(db: Session) -> None:
    # create tenant id=1 if it doesn't exist
    exists = db.execute(text("select id from tenants where id = :id"), {"id": DEFAULT_TENANT_ID}).fetchone()
    if not exists:
        db.execute(
            text("insert into tenants (id, name) values (:id, 'default')"),
            {"id": DEFAULT_TENANT_ID},
        )
        db.commit()

@router.post("/integrations/moodle/connect")
async def connect_moodle(payload: MoodleConnectPayload, db: Session = Depends(get_db)):
    """
    Saves Moodle URL + token, then tests connection.
    Returns Connected ✅ or error.
    """
    _ensure_tenants_table(db)
    _ensure_default_tenant(db)

    moodle_url = str(payload.moodle_url).rstrip("/")
    token = payload.token.strip()

    # Save config
    db.execute(
        text("""
            update tenants
               set moodle_url = :moodle_url,
                   moodle_token = :token
             where id = :id
        """),
        {"moodle_url": moodle_url, "token": token, "id": DEFAULT_TENANT_ID},
    )
    db.commit()

    # Test connection
    try:
        client = MoodleClient(moodle_url, token)
        info = await client.test_connection()

        return {
            "connected": True,
            "message": "Connected ✅",
            "site_name": info.get("sitename"),
            "moodle_username": info.get("username"),
            "moodle_release": info.get("release"),
            "moodle_version": info.get("version"),
        }
    except MoodleError as e:
        return {"connected": False, "message": f"Connection failed: {str(e)}"}
    except Exception as e:
        return {"connected": False, "message": f"Connection failed: {type(e).__name__}: {str(e)}"}

@router.get("/integrations/moodle/test")
async def test_moodle(db: Session = Depends(get_db)):
    """
    Tests Moodle connection using stored URL/token.
    """
    _ensure_tenants_table(db)
    _ensure_default_tenant(db)

    row = db.execute(
        text("select moodle_url, moodle_token from tenants where id = :id"),
        {"id": DEFAULT_TENANT_ID},
    ).fetchone()

    moodle_url = row[0]
    token = row[1]

    if not moodle_url or not token:
        return {"connected": False, "message": "Moodle not configured yet"}

    try:
        client = MoodleClient(moodle_url, token)
        info = await client.test_connection()

        return {
            "connected": True,
            "message": "Connected ✅",
            "site_name": info.get("sitename"),
            "moodle_username": info.get("username"),
            "moodle_release": info.get("release"),
            "moodle_version": info.get("version"),
        }
    except MoodleError as e:
        return {"connected": False, "message": f"Connection failed: {str(e)}"}
    except Exception as e:
        return {"connected": False, "message": f"Connection failed: {type(e).__name__}: {str(e)}"}



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

@router.post("/integrations/{tenant_id}/sync-courses")
async def sync_courses(tenant_id: int, db: Session = Depends(get_db)):
    """
    AC:
    - Clicking “Sync now” pulls courses and stores them for a tenant
    - Uses core_course_get_courses
    - Upsert (tenant_id, moodle_course_id)
    - Stores: id, name(fullname), summary
    """
    _ensure_courses_table(db)

    # 1) Load tenant Moodle config
    row = db.execute(
        text("select moodle_url, moodle_token from tenants where id = :id"),
        {"id": tenant_id},
    ).fetchone()

    if not row or not row[0] or not row[1]:
        return {"ok": False, "message": f"Tenant {tenant_id} not found or Moodle not configured"}

    moodle_url, moodle_token = row[0], row[1]

    # 2) Fetch courses from Moodle
    try:
        moodle = MoodleClient(moodle_url, moodle_token)
        courses = await moodle.call("core_course_get_courses")
    except MoodleError as e:
        return {"ok": False, "message": f"Moodle error: {str(e)}"}
    except Exception as e:
        return {"ok": False, "message": f"Failed to fetch courses: {type(e).__name__}: {str(e)}"}

    if not isinstance(courses, list):
        return {"ok": False, "message": "Unexpected response from Moodle (courses not a list)"}

    # 3) Prepare upsert rows
    rows = []
    for c in courses:
        # Moodle uses fullname; summary can be missing or HTML
        moodle_course_id = c.get("id")
        fullname = c.get("fullname") or ""
        summary = c.get("summary") or ""

        if not moodle_course_id or not fullname:
            continue

        rows.append({
            "tenant_id": tenant_id,
            "moodle_course_id": int(moodle_course_id),
            "fullname": fullname,
            "summary": summary,
        })

    # 4) Upsert into DB
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
        db.execute(upsert_sql, rows)  # executemany
        db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "fetched_from_moodle": len(courses),
        "upserted": len(rows),
        "message": "Sync complete ✅",
    }


@router.get("/integrations/{tenant_id}/moodle/users/exists")
async def moodle_user_exists(
    tenant_id: int,
    email: str = Query(..., min_length=3, description="User email to search in Moodle"),
    db: Session = Depends(get_db),
):
    """
    AC:
    - Given email, detect if user exists in Moodle (for a tenant)
    - Implement core_user_get_users by email
    - Return moodle_user_id if found
    """
    # 1) Load tenant moodle config
    row = db.execute(
        text("select moodle_url, moodle_token from tenants where id = :id"),
        {"id": tenant_id},
    ).fetchone()

    if not row or not row[0] or not row[1]:
        return {
            "ok": False,
            "message": "Tenant not found or Moodle not configured",
            "tenant_id": tenant_id,
        }

    moodle_url, moodle_token = row[0], row[1]

    # 2) Call Moodle core_user_get_users by email
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
            return {
                "ok": True,
                "exists": False,
                "email": email,
                "moodle_user_id": None,
            }

        # Moodle returns array; take the first match
        moodle_user_id = users[0].get("id")

        return {
            "ok": True,
            "exists": True,
            "email": email,
            "moodle_user_id": int(moodle_user_id) if moodle_user_id is not None else None,
        }

    except MoodleError as e:
        return {"ok": False, "message": f"Moodle error: {str(e)}"}
    except Exception as e:
        return {"ok": False, "message": f"Failed: {type(e).__name__}: {str(e)}"}