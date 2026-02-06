from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.welcome_course_email import send_welcome_course_email_for_tenant

router = APIRouter()

class SendWelcomeEmailPayload(BaseModel):
    tenant_id: int
    order_id: int

@router.post("/emails/welcome-course")
async def send_welcome_course_email(payload: SendWelcomeEmailPayload, db: Session = Depends(get_db)):
    try:
        return await send_welcome_course_email_for_tenant(
            db=db,
            tenant_id=int(payload.tenant_id),
            order_id=int(payload.order_id),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed sending email: {type(e).__name__}: {str(e)}",
        )