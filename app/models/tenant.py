from sqlalchemy import Column, BigInteger, Text, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(Text, nullable=False, default="default")
    moodle_url = Column(Text, nullable=True)
    moodle_token = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
