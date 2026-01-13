from sqlalchemy.orm import Session
from sqlalchemy import text
from app.services.moodle import MoodleClient

from sqlalchemy.orm import Session
from sqlalchemy import text
from app.services.moodle import MoodleClient

def _ensure_order_enrollments_table(db: Session):
    db.execute(text("""
        create table if not exists order_enrollments (
          id bigserial primary key,
          tenant_id bigint not null references tenants(id) on delete cascade,
          order_id bigint not null references orders(id) on delete cascade,
          moodle_course_id bigint not null,
          moodle_user_id bigint,
          status text not null,
          error text,
          created_at timestamptz not null default now(),
          unique (order_id, moodle_course_id)
        );
    """))
    db.commit()

async def enroll_user_into_product_courses_partial_ok(
    db: Session,
    tenant_id: int,
    order_id: int,
    product_id: int,
    moodle: MoodleClient,
    moodle_user_id: int,
    role_id: int = 5,
):
    _ensure_order_enrollments_table(db)

    rows = db.execute(
        text("""
            select moodle_course_id
              from product_courses
             where tenant_id = :t and product_id = :p
             order by moodle_course_id asc
        """),
        {"t": tenant_id, "p": product_id},
    ).fetchall()

    course_ids = [int(r[0]) for r in rows]
    if not course_ids:
        return {"ok": False, "message": "No courses linked to product"}

    successes = 0
    failures = 0
    results = []

    for course_id in course_ids:
        try:
            await moodle.call(
                "enrol_manual_enrol_users",
                **{
                    "enrolments[0][roleid]": role_id,
                    "enrolments[0][userid]": moodle_user_id,
                    "enrolments[0][courseid]": course_id,
                },
            )
            status = "success"
            error = None
            successes += 1
        except Exception as e:
            status = "failed"
            error = str(e)
            failures += 1

        # upsert per-course result
        db.execute(
            text("""
                insert into order_enrollments (tenant_id, order_id, moodle_course_id, moodle_user_id, status, error)
                values (:t, :o, :c, :u, :s, :e)
                on conflict (order_id, moodle_course_id)
                do update set status = excluded.status, error = excluded.error, moodle_user_id = excluded.moodle_user_id;
            """),
            {"t": tenant_id, "o": order_id, "c": course_id, "u": moodle_user_id, "s": status, "e": error},
        )
        db.commit()

        results.append({"course_id": course_id, "status": status, "error": error})

    return {
        "ok": True,
        "successes": successes,
        "failures": failures,
        "results": results,
    }