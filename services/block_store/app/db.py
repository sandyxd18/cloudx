from shared.db import get_db_url
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# ── Configuration ──────────────────────────────────────────────────────────────

DATABASE_URL    = get_db_url("cloudx")
# ── Database ───────────────────────────────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
# ── FastAPI app ────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]
