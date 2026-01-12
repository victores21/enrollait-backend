from fastapi import APIRouter, Depends
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
