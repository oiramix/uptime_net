from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..main_deps import get_db
from ..models import Incident, VerifiedResult


router = APIRouter(prefix="/v1", tags=["status"])


class VerifiedResultOut(BaseModel):
    target_id: str
    region_id: str
    check_type: str
    window_start: datetime
    ok: bool
    total_ms_median: int
    ttfb_ms_median: Optional[int]

    class Config:
        orm_mode = True


class IncidentOut(BaseModel):
    incident_id: str
    target_id: str
    region_id: str
    check_type: str
    status: str
    cause: str
    opened_at: datetime
    closed_at: Optional[datetime]

    class Config:
        orm_mode = True


@router.get("/status", response_model=VerifiedResultOut)
def get_latest_status(
    target_id: str = Query(...),
    db: Session = Depends(get_db),
):
    # For MVP, we use global region and http check_type.
    vr = (
        db.query(VerifiedResult)
        .filter(
            VerifiedResult.target_id == target_id,
            VerifiedResult.region_id == "global",
            VerifiedResult.check_type == "http",
        )
        .order_by(VerifiedResult.window_start.desc())
        .first()
    )
    if not vr:
        raise HTTPException(status_code=404, detail="No verified result for target")
    return vr


@router.get("/incidents", response_model=List[IncidentOut])
def list_incidents(
    target_id: str = Query(...),
    db: Session = Depends(get_db),
):
    incidents = (
        db.query(Incident)
        .filter(Incident.target_id == target_id)
        .order_by(Incident.opened_at.desc())
        .all()
    )
    return incidents

