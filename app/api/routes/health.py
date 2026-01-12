from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.db import get_db

router = APIRouter()

@router.get("/health")
def health(db:Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"Enrollait ok": True}
    except Exception as e:
        return {"Enrollait ok": False, "Error": str(e)}
