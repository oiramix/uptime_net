from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from ..main_deps import get_db
from ..models import Target
from ..core.security import gen_id


router = APIRouter(prefix="/v1/targets", tags=["targets"])


class TargetCreate(BaseModel):
    url: HttpUrl
    interval_s: int = 60
    latency_threshold_ms: Optional[int] = None


class TargetOut(BaseModel):
    target_id: str
    url: str
    interval_s: int
    latency_threshold_ms: int
    check_http: bool
    check_tls: bool
    check_dns: bool
    created_at: datetime

    class Config:
        orm_mode = True


@router.post("", response_model=TargetOut)
def create_target(payload: TargetCreate, db: Session = Depends(get_db)):
    tid = gen_id("t")
    latency = payload.latency_threshold_ms if payload.latency_threshold_ms is not None else 2000
    t = Target(
        target_id=tid,
        url=str(payload.url),
        interval_s=payload.interval_s,
        latency_threshold_ms=latency,
        check_http=True,
        check_tls=True,
        check_dns=False,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.get("", response_model=List[TargetOut])
def list_targets(db: Session = Depends(get_db)):
    return db.query(Target).order_by(Target.created_at.asc()).all()

