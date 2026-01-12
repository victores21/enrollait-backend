from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, EmailStr
import re
import secrets
import string

from app.core.db import get_db
from app.services.moodle import MoodleClient, MoodleError

router = APIRouter()

class CreateMoodleUserPayload(BaseModel):
    email: EmailStr
    firstname: str | None = None
    lastname: str | None = None

def _ensure_user_map_table(db: Session) -> None:
    db.execute(text("""
        create table if not exists user_map (
          id bigserial primary key,
          tenant_id bigint not null references tenants(id) on delete cascade,
          email text not null,
          moodle_user_id bigint not null,
          created_at timestamptz not null default now(),
          unique (tenant_id, email)
        );
    """))
    db.commit()

def _gen_username(email: str) -> str:
    # Moodle usernames are usually lowercase, no spaces, limited chars
    base = email.split("@")[0].lower()
    base = re.sub(r"[^a-z0-9._-]+", "", base)
    base = base[:18] if base else "user"
    suffix = secrets.token_hex(3)  # 6 chars
    return f"{base}_{suffix}"

def _gen_temp_password() -> str:
    # Strong random password (we will NOT email it)
    alphabet = string.ascii_letters + string.digits + "!@#$%*_-"
    return "".join(secrets.choice(alphabet) for _ in range(16))

@router.post("/integrations/{tenant_id}/moodle/users/ensure")
async def ensure_moodle_user(
    tenant_id: int,
    payload: CreateMoodleUserPayload,
    db: Session = Depends(get_db),
):
    """
    AC:
    - If not found, create user successfully.
    Checklist:
    - Implement core_user_create_users
    - Generate username automatically
    - Store mapping in user_map
    """
    _ensure_user_map_table(db)

    # 1) Load tenant moodle config
    row = db.execute(
        text("select moodle_url, moodle_token from tenants where id = :id"),
        {"id": tenant_id},
    ).fetchone()

    if not row or not row[0] or not row[1]:
        return {"ok": False, "message": "Tenant not found or Moodle not configured", "tenant_id": tenant_id}

    moodle_url, moodle_token = row[0], row[1]
    email = payload.email.strip().lower()

    moodle = MoodleClient(moodle_url, moodle_token)

    # 2) First check if user exists in Moodle (by email)
    try:
        data = await moodle.call(
            "core_user_get_users",
            **{
                "criteria[0][key]": "email",
                "criteria[0][value]": email,
            },
        )
        users = data.get("users", []) if isinstance(data, dict) else []
    except MoodleError as e:
        return {"ok": False, "message": f"Moodle error (search): {str(e)}"}
    except Exception as e:
        return {"ok": False, "message": f"Failed (search): {type(e).__name__}: {str(e)}"}

    # If exists, store mapping and return
    if users:
        moodle_user_id = int(users[0]["id"])

        db.execute(
            text("""
                insert into user_map (tenant_id, email, moodle_user_id)
                values (:tenant_id, :email, :moodle_user_id)
                on conflict (tenant_id, email)
                do update set moodle_user_id = excluded.moodle_user_id;
            """),
            {"tenant_id": tenant_id, "email": email, "moodle_user_id": moodle_user_id},
        )
        db.commit()

        return {
            "ok": True,
            "created": False,
            "exists": True,
            "email": email,
            "moodle_user_id": moodle_user_id,
        }

    # 3) Create user in Moodle
    username = _gen_username(email)
    firstname = (payload.firstname or "Student").strip()[:100]
    lastname = (payload.lastname or "User").strip()[:100]
    temp_password = _gen_temp_password()

    try:
        created = await moodle.call(
            "core_user_create_users",
            **{
                "users[0][username]": username,
                "users[0][password]": temp_password,
                "users[0][firstname]": firstname,
                "users[0][lastname]": lastname,
                "users[0][email]": email,
            },
        )
        # created is usually a list like [{"id": 123}]
        moodle_user_id = int(created[0]["id"])
    except MoodleError as e:
        return {"ok": False, "message": f"Moodle error (create): {str(e)}"}
    except Exception as e:
        return {"ok": False, "message": f"Failed (create): {type(e).__name__}: {str(e)}"}

    # 4) Store mapping in DB
    db.execute(
        text("""
            insert into user_map (tenant_id, email, moodle_user_id)
            values (:tenant_id, :email, :moodle_user_id)
            on conflict (tenant_id, email)
            do update set moodle_user_id = excluded.moodle_user_id;
        """),
        {"tenant_id": tenant_id, "email": email, "moodle_user_id": moodle_user_id},
    )
    db.commit()

    return {
        "ok": True,
        "created": True,
        "exists": False,
        "email": email,
        "moodle_user_id": moodle_user_id,
        "username": username,
        "note": "Password was generated server-side; do not email it. Use Moodle 'Forgot password' flow.",
    }
