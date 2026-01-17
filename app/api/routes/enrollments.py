# from fastapi import APIRouter, Depends
# from sqlalchemy.orm import Session
# from sqlalchemy import text
# from pydantic import BaseModel, EmailStr

# from app.core.db import get_db
# from app.services.moodle import MoodleClient, MoodleError

# router = APIRouter()

# class ManualEnrollPayload(BaseModel):
#     email: EmailStr
#     moodle_course_id: int
#     role_id: int | None = None  # default student role if not provided

# def _get_tenant_moodle(db: Session, tenant_id: int):
#     row = db.execute(
#         text("select moodle_url, moodle_token from tenants where id = :id"),
#         {"id": tenant_id},
#     ).fetchone()
#     if not row or not row[0] or not row[1]:
#         return None
#     return row[0], row[1]

# async def _find_moodle_user_id(moodle: MoodleClient, email: str) -> int | None:
#     data = await moodle.call(
#         "core_user_get_users",
#         **{
#             "criteria[0][key]": "email",
#             "criteria[0][value]": email,
#         },
#     )
#     users = data.get("users", []) if isinstance(data, dict) else []
#     if not users:
#         return None
#     return int(users[0]["id"])

# async def _manual_enrolment_enabled(moodle: MoodleClient, course_id: int) -> bool:
#     """
#     Checks if enrol_manual plugin is enabled for the course by inspecting enrolment instances.
#     Requires function: core_enrol_get_course_enrolment_methods
#     If not available/allowed, we'll return True and rely on actual enroll call error.
#     """
#     try:
#         methods = await moodle.call(
#             "core_enrol_get_course_enrolment_methods",
#             **{"courseid": course_id},
#         )
#         if not isinstance(methods, list):
#             return True

#         # manual enrolment method = type "manual"
#         for m in methods:
#             if m.get("type") == "manual" and m.get("status") == 0:
#                 return True
#         return False
#     except Exception:
#         # If the site doesn't allow this function or permissions block it,
#         # don't block MVP; let enroll call be the source of truth.
#         return True
# async def _manual_enrolment_state(moodle: MoodleClient, course_id: int) -> bool | None:
#     """
#     Returns:
#       True  -> manual is present and enabled
#       False -> manual is present and disabled
#       None  -> unknown (manual not returned due to permissions / Moodle behavior)
#     """
#     try:
#         methods = await moodle.call(
#             "core_enrol_get_course_enrolment_methods",
#             **{"courseid": course_id},
#         )
#     except Exception:
#         return None

#     if not isinstance(methods, list) or not methods:
#         return None

#     # Look for manual. If not present, we can't conclude it's disabled.
#     manual = None
#     for m in methods:
#         if (m.get("type") or "").lower() == "manual":
#             manual = m
#             break

#     if manual is None:
#         return None  # <- this is your case right now

#     # Moodle status is usually 0 enabled, 1 disabled (sometimes comes back weird)
#     status = manual.get("status")
#     status_str = str(status) if status is not None else "0"
#     return status_str in ("0", "false", "False")

# @router.post("/integrations/{tenant_id}/moodle/enroll/manual")
# async def enroll_manual(
#     tenant_id: int,
#     payload: ManualEnrollPayload,
#     db: Session = Depends(get_db),
# ):
#     """
#     AC:
#     - User is enrolled after successful payment.
#     Checklist:
#     - Implement enrol_manual_enrol_users
#     - Confirm manual enrollment enabled in Moodle course
#     """
#     tenant_conf = _get_tenant_moodle(db, tenant_id)
#     if not tenant_conf:
#         return {"ok": False, "message": "Tenant not found or Moodle not configured", "tenant_id": tenant_id}

#     moodle_url, moodle_token = tenant_conf
#     moodle = MoodleClient(moodle_url, moodle_token)

#     email = payload.email.strip().lower()
#     course_id = int(payload.moodle_course_id)
#     # role_id = int(payload.role_id) if payload.role_id is not None else 5  # 5 is commonly "student"
#     role_id = payload.role_id if payload.role_id and int(payload.role_id) > 0 else 5
#     if payload.role_id is not None and int(payload.role_id) <= 0:
#         return {"ok": False, "message": "role_id must be > 0 (use Student role id, commonly 5)"}


#     # 1) Find moodle user
#     try:
#         moodle_user_id = await _find_moodle_user_id(moodle, email)
#     except MoodleError as e:
#         return {"ok": False, "message": f"Moodle error (find user): {str(e)}"}
#     except Exception as e:
#         return {"ok": False, "message": f"Failed (find user): {type(e).__name__}: {str(e)}"}

#     if not moodle_user_id:
#         return {"ok": False, "message": "User not found in Moodle. Create user first.", "email": email}

#     # 2) Confirm manual enrolment enabled (best-effort)
#     # enabled = await _manual_enrolment_enabled(moodle, course_id)
#     # if not enabled:
#     #     return {
#     #         "ok": False,
#     #         "message": "Manual enrollment is not enabled for this course. Enable 'Manual enrolments' in Moodle.",
#     #         "course_id": course_id,
#     #     }
#     manual_state = await _manual_enrolment_state(moodle, course_id)

#     # Only block if Moodle explicitly tells us manual is disabled.
#     # If manual is missing from the list, treat it as "unknown" and try enrolment anyway.
#     if manual_state is False:
#         return {
#             "ok": False,
#             "message": "Manual enrollment is disabled for this course. Enable 'Manual enrolments' in Moodle.",
#             "course_id": course_id,
#         }    

#     # 3) Enroll user (manual)
#     try:
#         await moodle.call(
#             "enrol_manual_enrol_users",
#             **{
#                 "enrolments[0][roleid]": role_id,
#                 "enrolments[0][userid]": moodle_user_id,
#                 "enrolments[0][courseid]": course_id,
#             },
#         )
#     except MoodleError as e:
#         return {"ok": False, "message": f"Moodle error (enroll): {str(e)}"}
#     except Exception as e:
#         return {"ok": False, "message": f"Failed (enroll): {type(e).__name__}: {str(e)}"}

#     return {
#         "ok": True,
#         "message": "Enrolled ✅",
#         "tenant_id": tenant_id,
#         "email": email,
#         "moodle_user_id": moodle_user_id,
#         "course_id": course_id,
#         "role_id": role_id,
#     }


# @router.get("/integrations/{tenant_id}/moodle/course/{course_id}/enrolment-methods")
# async def enrolment_methods(tenant_id: int, course_id: int, db: Session = Depends(get_db)):
#     row = db.execute(
#         text("select moodle_url, moodle_token from tenants where id = :id"),
#         {"id": tenant_id},
#     ).fetchone()

#     if not row or not row[0] or not row[1]:
#         return {"ok": False, "message": "Tenant not found or Moodle not configured"}

#     moodle = MoodleClient(row[0], row[1])

#     try:
#         methods = await moodle.call(
#             "core_enrol_get_course_enrolment_methods",
#             **{"courseid": int(course_id)},
#         )
#         return {"ok": True, "course_id": course_id, "methods": methods}
#     except MoodleError as e:
#         return {"ok": False, "message": f"Moodle error: {str(e)}"}
#     except Exception as e:
#         return {"ok": False, "message": f"{type(e).__name__}: {str(e)}"}

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, EmailStr

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request  # ✅ new tenant resolver
from app.services.moodle import MoodleClient, MoodleError

router = APIRouter()


class ManualEnrollPayload(BaseModel):
    email: EmailStr
    moodle_course_id: int
    role_id: int | None = None  # default student role if not provided


def _get_tenant_moodle(db: Session, tenant_id: int):
    row = db.execute(
        text("select moodle_url, moodle_token from tenants where id = :id"),
        {"id": tenant_id},
    ).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    return row[0], row[1]


async def _find_moodle_user_id(moodle: MoodleClient, email: str) -> int | None:
    data = await moodle.call(
        "core_user_get_users",
        **{
            "criteria[0][key]": "email",
            "criteria[0][value]": email,
        },
    )
    users = data.get("users", []) if isinstance(data, dict) else []
    if not users:
        return None
    return int(users[0]["id"])


async def _manual_enrolment_state(moodle: MoodleClient, course_id: int) -> bool | None:
    """
    Returns:
      True  -> manual is present and enabled
      False -> manual is present and disabled
      None  -> unknown (manual not returned due to permissions / Moodle behavior)
    """
    try:
        methods = await moodle.call(
            "core_enrol_get_course_enrolment_methods",
            **{"courseid": int(course_id)},
        )
    except Exception:
        return None

    if not isinstance(methods, list) or not methods:
        return None

    manual = None
    for m in methods:
        if (m.get("type") or "").lower() == "manual":
            manual = m
            break

    if manual is None:
        return None

    status = manual.get("status")
    status_str = str(status) if status is not None else "0"
    return status_str in ("0", "false", "False")


# ✅ NEW endpoint (tenant inferred)
@router.post("/integrations/moodle/enroll/manual")
async def enroll_manual(
    payload: ManualEnrollPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    """
    AC:
    - User is enrolled after successful payment.
    Checklist:
    - Implement enrol_manual_enrol_users
    - Confirm manual enrollment enabled in Moodle course (best-effort)
    """
    tenant_conf = _get_tenant_moodle(db, tenant_id)
    if not tenant_conf:
        return {"ok": False, "message": "Tenant not found or Moodle not configured", "tenant_id": tenant_id}

    moodle_url, moodle_token = tenant_conf
    moodle = MoodleClient(moodle_url, moodle_token)

    email = payload.email.strip().lower()
    course_id = int(payload.moodle_course_id)
    role_id = payload.role_id if payload.role_id and int(payload.role_id) > 0 else 5

    if payload.role_id is not None and int(payload.role_id) <= 0:
        return {"ok": False, "message": "role_id must be > 0 (use Student role id, commonly 5)"}

    # 1) Find moodle user
    try:
        moodle_user_id = await _find_moodle_user_id(moodle, email)
    except MoodleError as e:
        return {"ok": False, "message": f"Moodle error (find user): {str(e)}", "tenant_id": tenant_id}
    except Exception as e:
        return {"ok": False, "message": f"Failed (find user): {type(e).__name__}: {str(e)}", "tenant_id": tenant_id}

    if not moodle_user_id:
        return {"ok": False, "message": "User not found in Moodle. Create user first.", "email": email, "tenant_id": tenant_id}

    # 2) Confirm manual enrolment enabled (best-effort)
    manual_state = await _manual_enrolment_state(moodle, course_id)
    if manual_state is False:
        return {
            "ok": False,
            "message": "Manual enrollment is disabled for this course. Enable 'Manual enrolments' in Moodle.",
            "course_id": course_id,
            "tenant_id": tenant_id,
        }

    # 3) Enroll user (manual)
    try:
        await moodle.call(
            "enrol_manual_enrol_users",
            **{
                "enrolments[0][roleid]": int(role_id),
                "enrolments[0][userid]": int(moodle_user_id),
                "enrolments[0][courseid]": int(course_id),
            },
        )
    except MoodleError as e:
        return {"ok": False, "message": f"Moodle error (enroll): {str(e)}", "tenant_id": tenant_id}
    except Exception as e:
        return {"ok": False, "message": f"Failed (enroll): {type(e).__name__}: {str(e)}", "tenant_id": tenant_id}

    return {
        "ok": True,
        "message": "Enrolled ✅",
        "tenant_id": tenant_id,
        "email": email,
        "moodle_user_id": moodle_user_id,
        "course_id": course_id,
        "role_id": int(role_id),
        "manual_state": manual_state,  # True/None (useful debug)
    }


# ✅ NEW endpoint (tenant inferred)
@router.get("/integrations/moodle/course/{course_id}/enrolment-methods")
async def enrolment_methods(
    course_id: int,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    row = db.execute(
        text("select moodle_url, moodle_token from tenants where id = :id"),
        {"id": tenant_id},
    ).fetchone()

    if not row or not row[0] or not row[1]:
        return {"ok": False, "message": "Tenant not found or Moodle not configured", "tenant_id": tenant_id}

    moodle = MoodleClient(row[0], row[1])

    try:
        methods = await moodle.call(
            "core_enrol_get_course_enrolment_methods",
            **{"courseid": int(course_id)},
        )
        return {"ok": True, "tenant_id": tenant_id, "course_id": int(course_id), "methods": methods}
    except MoodleError as e:
        return {"ok": False, "tenant_id": tenant_id, "message": f"Moodle error: {str(e)}"}
    except Exception as e:
        return {"ok": False, "tenant_id": tenant_id, "message": f"{type(e).__name__}: {str(e)}"}