from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
import json
from datetime import datetime, timezone
from typing import Any, Literal

from app.core.db import get_db
from app.core.tenant import get_tenant_id_from_request

router = APIRouter()

# -----------------------------
# Onboarding definition
# -----------------------------
OnboardingStepKey = Literal[
    "connect-moodle",
    "sync-moodle",
    "connect-stripe",
    "test-purchase",
]

STEPS_ORDER: list[dict[str, Any]] = [
    {"key": "connect-moodle", "label": "Connect Moodle", "order": 1},
    {"key": "sync-moodle", "label": "Sync Moodle", "order": 2},
    {"key": "connect-stripe", "label": "Connect Stripe", "order": 3},
    {"key": "test-purchase", "label": "Create product", "order": 4},
]

# -----------------------------
# DB helpers
# -----------------------------
def _ensure_onboarding_table(db: Session) -> None:
    """
    Creates the table if missing and ensures required columns exist.
    This is a "runtime migration" approach.
    """
    db.execute(
        text(
            """
            create table if not exists tenant_onboarding (
              tenant_id bigint primary key references tenants(id) on delete cascade,
              steps jsonb not null default '{}'::jsonb,
              updated_at timestamptz not null default now()
            );
            """
        )
    )

    # Ensure the new column exists for older deployments
    db.execute(
        text(
            """
            alter table tenant_onboarding
            add column if not exists admin_welcome_seen boolean not null default false;
            """
        )
    )

    db.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_steps(existing_steps: dict[str, Any] | None) -> dict[str, Any]:
    base: dict[str, Any] = {}
    for s in STEPS_ORDER:
        k = s["key"]
        base[k] = {"done": False, "meta": {}, "completed_at": None}

    if isinstance(existing_steps, dict):
        for k, v in existing_steps.items():
            if k in base and isinstance(v, dict):
                base[k]["done"] = bool(v.get("done", base[k]["done"]))
                base[k]["meta"] = (
                    v.get("meta", base[k]["meta"])
                    if isinstance(v.get("meta", {}), dict)
                    else base[k]["meta"]
                )
                base[k]["completed_at"] = v.get("completed_at", base[k]["completed_at"])

    return base


def _compute_state(steps_obj: dict[str, Any]) -> dict[str, Any]:
    total = len(STEPS_ORDER)
    done_count = 0
    current = None

    for s in STEPS_ORDER:
        k = s["key"]
        if steps_obj.get(k, {}).get("done") is True:
            done_count += 1
        elif current is None:
            current = s

    if current is None:
        current = STEPS_ORDER[-1]

    percent = int(round((done_count / total) * 100)) if total else 0

    steps_list = []
    for s in STEPS_ORDER:
        k = s["key"]
        steps_list.append(
            {
                "key": k,
                "label": s["label"],
                "order": s["order"],
                "done": bool(steps_obj.get(k, {}).get("done")),
                "completed_at": steps_obj.get(k, {}).get("completed_at"),
                "meta": steps_obj.get(k, {}).get("meta") or {},
            }
        )

    return {
        "steps": steps_list,
        "current_step": current,
        "progress": {"done": done_count, "total": total, "percent": percent},
    }


def _get_or_create_onboarding_row(db: Session, tenant_id: int) -> tuple[dict[str, Any], bool]:
    """
    Returns (steps_obj, admin_welcome_seen).
    If row doesn't exist, creates it with normalized steps and admin_welcome_seen=false.
    """
    row = db.execute(
        text(
            """
            select steps, admin_welcome_seen
              from tenant_onboarding
             where tenant_id = :t
             limit 1
            """
        ),
        {"t": int(tenant_id)},
    ).fetchone()

    if row:
        existing_steps = row[0] if row[0] else {}
        admin_welcome_seen = bool(row[1])
        return _normalize_steps(existing_steps), admin_welcome_seen

    # Create row if missing (important so modal isn't "first time" forever)
    steps_obj = _normalize_steps({})
    steps_json = json.dumps(steps_obj)
    db.execute(
        text(
            """
            insert into tenant_onboarding (tenant_id, steps, admin_welcome_seen, updated_at)
            values (:t, CAST(:steps AS jsonb), false, now())
            on conflict (tenant_id)
            do nothing
            """
        ),
        {"t": int(tenant_id), "steps": steps_json},
    )
    db.commit()
    return steps_obj, False


# -----------------------------
# API models
# -----------------------------
class OnboardingSetStepPayload(BaseModel):
    step: OnboardingStepKey
    done: bool = True
    meta: dict[str, Any] | None = None


class OnboardingStateResponse(BaseModel):
    ok: bool
    tenant_id: int
    steps: list[dict[str, Any]]
    current_step: dict[str, Any]
    progress: dict[str, Any]
    admin_welcome_seen: bool
    show_admin_welcome_modal: bool


class AdminWelcomeSeenPayload(BaseModel):
    seen: bool = True


class AdminWelcomeSeenResponse(BaseModel):
    ok: bool
    tenant_id: int
    admin_welcome_seen: bool
    show_admin_welcome_modal: bool


# -----------------------------
# Endpoints
# -----------------------------
@router.get("/onboarding/state", response_model=OnboardingStateResponse)
def get_onboarding_state(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    _ensure_onboarding_table(db)

    steps_obj, admin_welcome_seen = _get_or_create_onboarding_row(db, int(tenant_id))
    state = _compute_state(steps_obj)

    show_modal = not admin_welcome_seen

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        **state,
        "admin_welcome_seen": admin_welcome_seen,
        "show_admin_welcome_modal": show_modal,
    }


@router.post("/onboarding/step", response_model=OnboardingStateResponse)
def set_onboarding_step(
    payload: OnboardingSetStepPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    _ensure_onboarding_table(db)

    steps_obj, admin_welcome_seen = _get_or_create_onboarding_row(db, int(tenant_id))

    step_key = payload.step
    steps_obj[step_key]["done"] = bool(payload.done)

    if payload.meta and isinstance(payload.meta, dict):
        current_meta = steps_obj[step_key].get("meta") or {}
        if not isinstance(current_meta, dict):
            current_meta = {}
        current_meta.update(payload.meta)
        steps_obj[step_key]["meta"] = current_meta

    if payload.done:
        steps_obj[step_key]["completed_at"] = _now_iso()
    else:
        steps_obj[step_key]["completed_at"] = None

    steps_json = json.dumps(steps_obj)

    try:
        db.execute(
            text(
                """
                insert into tenant_onboarding (tenant_id, steps, updated_at)
                values (:t, CAST(:steps AS jsonb), now())
                on conflict (tenant_id)
                do update set
                  steps = excluded.steps,
                  updated_at = now()
                """
            ),
            {"t": int(tenant_id), "steps": steps_json},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update onboarding step: {type(e).__name__}: {str(e)}",
        )

    state = _compute_state(steps_obj)
    show_modal = not admin_welcome_seen

    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        **state,
        "admin_welcome_seen": admin_welcome_seen,
        "show_admin_welcome_modal": show_modal,
    }


@router.post("/onboarding/admin-welcome/seen", response_model=AdminWelcomeSeenResponse)
def set_admin_welcome_seen(
    payload: AdminWelcomeSeenPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    _ensure_onboarding_table(db)

    # Ensure row exists
    _get_or_create_onboarding_row(db, int(tenant_id))

    try:
        db.execute(
            text(
                """
                update tenant_onboarding
                   set admin_welcome_seen = :seen,
                       updated_at = now()
                 where tenant_id = :t
                """
            ),
            {"t": int(tenant_id), "seen": bool(payload.seen)},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update admin_welcome_seen: {type(e).__name__}: {str(e)}",
        )

    admin_welcome_seen = bool(payload.seen)
    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        "admin_welcome_seen": admin_welcome_seen,
        "show_admin_welcome_modal": not admin_welcome_seen,
    }