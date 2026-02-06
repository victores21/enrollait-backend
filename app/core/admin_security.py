from __future__ import annotations

import os
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt

JWT_SECRET = os.getenv("ADMIN_JWT_SECRET", "dev-change-me")
JWT_ALG = os.getenv("ADMIN_JWT_ALG", "HS256")
JWT_EXPIRES_MIN = int(os.getenv("ADMIN_JWT_EXPIRES_MIN", "10080"))  # 7 days


def hash_password(password: str) -> str:
    # bcrypt limit: 72 bytes (utf-8)
    pw_bytes = (password or "").encode("utf-8")
    if len(pw_bytes) > 72:
        raise ValueError("password too long (max 72 bytes)")

    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        pw_bytes = (password or "").encode("utf-8")
        ph_bytes = (password_hash or "").encode("utf-8")
        return bcrypt.checkpw(pw_bytes, ph_bytes)
    except Exception:
        return False


def create_admin_token(*, tenant_id: int, admin_user_id: int, email: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "typ": "admin",
        "tid": int(tenant_id),
        "uid": int(admin_user_id),
        "email": str(email).lower(),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRES_MIN)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_admin_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])