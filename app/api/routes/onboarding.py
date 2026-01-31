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
    {"key": "test-purchase", "label": "Test Purchase", "order": 4},
]


# -----------------------------
# DB helpers
# -----------------------------
def _ensure_onboarding_table(db: Session) -> None:
    """
    Creates the table if missing.
    You can migrate this properly later, but this makes it work immediately.
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
    db.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_steps(existing_steps: dict[str, Any] | None) -> dict[str, Any]:
    """
    Ensure all known steps exist in the steps json with structure.
    """
    base: dict[str, Any] = {}
    for s in STEPS_ORDER:
        k = s["key"]
        base[k] = {
            "done": False,
            "meta": {},
            "completed_at": None,
        }

    if isinstance(existing_steps, dict):
        # merge existing values
        for k, v in existing_steps.items():
            if k in base and isinstance(v, dict):
                base[k]["done"] = bool(v.get("done", base[k]["done"]))
                base[k]["meta"] = v.get("meta", base[k]["meta"]) if isinstance(v.get("meta", {}), dict) else base[k]["meta"]
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
        # all done -> current is last step
        current = STEPS_ORDER[-1]

    percent = int(round((done_count / total) * 100)) if total else 0

    # return as array (nice for frontend)
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


# -----------------------------
# Endpoints
# -----------------------------
@router.get("/onboarding/state", response_model=OnboardingStateResponse)
def get_onboarding_state(
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    _ensure_onboarding_table(db)

    row = db.execute(
        text(
            """
            select steps
              from tenant_onboarding
             where tenant_id = :t
             limit 1
            """
        ),
        {"t": int(tenant_id)},
    ).fetchone()

    existing_steps = row[0] if row and row[0] else {}
    steps_obj = _normalize_steps(existing_steps)

    state = _compute_state(steps_obj)
    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        **state,
    }


@router.post("/onboarding/step", response_model=OnboardingStateResponse)
def set_onboarding_step(
    payload: OnboardingSetStepPayload,
    tenant_id: int = Depends(get_tenant_id_from_request),
    db: Session = Depends(get_db),
):
    _ensure_onboarding_table(db)

    # 1) fetch existing
    row = db.execute(
        text(
            """
            select steps
              from tenant_onboarding
             where tenant_id = :t
             limit 1
            """
        ),
        {"t": int(tenant_id)},
    ).fetchone()

    existing_steps = row[0] if row and row[0] else {}
    steps_obj = _normalize_steps(existing_steps)

    # 2) update one step
    step_key = payload.step
    steps_obj[step_key]["done"] = bool(payload.done)

    if payload.meta and isinstance(payload.meta, dict):
        # merge meta
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

    # 3) upsert (IMPORTANT: CAST(:steps AS jsonb))
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
    return {
        "ok": True,
        "tenant_id": int(tenant_id),
        **state,
    }