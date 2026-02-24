from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.canonical_json import canonical_dumps, strip_keys_deep
from shared.ed25519 import verify_bytes

from ..core.config import Settings
from ..core.keys import get_or_create_server_sk_b64
from ..core.security import gen_id, gen_token
from ..main_deps import get_db, get_settings_cached
from ..models import Job, Node, Receipt
from ..core.auth import get_current_node

router = APIRouter(prefix="/v1/node", tags=["node"])


class RegisterReq(BaseModel):
    node_pubkey: str
    node_version: str
    platform: Optional[str] = None
    capabilities: Dict[str, Any] = Field(default_factory=dict)


class RegisterResp(BaseModel):
    node_id: str
    token: str
    server_time: str


@router.post("/register", response_model=RegisterResp)
def register(req: RegisterReq, db: Session = Depends(get_db)):
    # Basic pubkey shape validation (base64 decodes) is done by verify_bytes later.
    # Upsert by pubkey.
    existing = db.query(Node).filter(Node.pubkey_b64 == req.node_pubkey).one_or_none()
    if existing:
        existing.last_seen_at = datetime.utcnow()
        db.commit()
        return RegisterResp(node_id=existing.node_id, token=existing.token, server_time=datetime.utcnow().isoformat() + "Z")

    node = Node(
        node_id=gen_id("n"),
        pubkey_b64=req.node_pubkey,
        token=gen_token(),
        created_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        status="active",
    )
    db.add(node)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # race: fetch again
        node = db.query(Node).filter(Node.pubkey_b64 == req.node_pubkey).one()
    return RegisterResp(node_id=node.node_id, token=node.token, server_time=datetime.utcnow().isoformat() + "Z")


class JobOut(BaseModel):
    job_id: str
    target_id: str
    check_type: str
    params: Dict[str, Any]
    nonce: str
    issued_at: str
    expires_at: str
    server_sig: str


class JobsResp(BaseModel):
    jobs: List[JobOut]


@router.get("/jobs", response_model=JobsResp)
def fetch_jobs(
    limit: int = Query(default=5, ge=1, le=50),
    node: Node = Depends(get_current_node),
    db: Session = Depends(get_db),
):
    # Claim unassigned, not-yet-expired jobs using SKIP LOCKED.
    stmt = (
        select(Job)
        .where(Job.node_id.is_(None))
        .where(Job.expires_at > datetime.utcnow())
        .order_by(Job.issued_at.desc())
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()
    out: List[JobOut] = []
    now = datetime.utcnow()
    for job in rows:
        job.node_id = node.node_id
        job.claimed_at = now
        out.append(
            JobOut(
                job_id=job.job_id,
                target_id=job.target_id,
                check_type=job.check_type,
                params=json.loads(job.params_json),
                nonce=job.nonce,
                issued_at=job.issued_at.isoformat() + "Z",
                expires_at=job.expires_at.isoformat() + "Z",
                server_sig=job.server_sig_b64,
            )
        )
    node.last_seen_at = now
    db.commit()
    return JobsResp(jobs=out)


class ReceiptIn(BaseModel):
    job_id: str
    node_id: str
    nonce: str
    started_at: str
    finished_at: str
    result: Dict[str, Any]
    timings_ms: Dict[str, int]
    fingerprints: Dict[str, Any]
    node_sig: str


class ReceiptResp(BaseModel):
    accepted: bool
    server_time: str


@router.post("/receipts", response_model=ReceiptResp)
def submit_receipt(
    receipt: ReceiptIn,
    node: Node = Depends(get_current_node),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_cached),
):
    if receipt.node_id != node.node_id:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "node_id_mismatch", "message": "node_id does not match authenticated node"},
        )

    job = db.query(Job).filter(Job.job_id == receipt.job_id).one_or_none()
    if not job:
        raise HTTPException(
            status_code=404,
            detail={"reason_code": "job_not_found", "message": "job_id not found"},
        )
    if job.node_id != node.node_id:
        raise HTTPException(
            status_code=403,
            detail={"reason_code": "job_not_assigned", "message": "job is not assigned to this node"},
        )
    if receipt.nonce != job.nonce:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "nonce_mismatch", "message": "nonce does not match job"},
        )

    started_at = _parse_ts(receipt.started_at)
    finished_at = _parse_ts(receipt.finished_at)
    if finished_at < started_at:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "finished_before_started", "message": "finished_at is before started_at"},
        )

    # TTL and assignment checks:
    # - require job was claimed server-side
    # - reject if claim happened after expiry
    # - accept receipt if it arrives within a small grace window after expiry
    if job.claimed_at is None:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "job_not_claimed", "message": "job has not been claimed by any node"},
        )
    if job.claimed_at > job.expires_at:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "job_claim_after_expiry", "message": "job was claimed after expiry"},
        )
    now = datetime.utcnow()
    if now > job.expires_at + timedelta(seconds=60):
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "receipt_too_late", "message": "receipt arrived after allowed grace window"},
        )

    # Timeout: finished-start must be <= timeout_ms
    timeout_ms = int(_get_timeout_ms(job.params_json, settings.http_total_timeout_ms))
    dur_ms = int((finished_at - started_at).total_seconds() * 1000)
    if dur_ms > timeout_ms + 500:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "duration_exceeded", "message": "receipt duration exceeds timeout"},
        )

    # Verify signature
    receipt_dict = receipt.model_dump()
    unsigned = strip_keys_deep(receipt_dict, keys=["node_sig"])
    msg = canonical_dumps(unsigned)
    if not verify_bytes(node.pubkey_b64, msg, receipt.node_sig):
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "invalid_signature", "message": "node_sig could not be verified"},
        )

    # Store
    r = Receipt(
        receipt_id=gen_id("rcpt"),
        job_id=job.job_id,
        node_id=node.node_id,
        nonce=receipt.nonce,
        started_at=started_at,
        finished_at=finished_at,
        receipt_json=json.dumps(receipt_dict, separators=(",", ":"), ensure_ascii=False),
        accepted=True,
    )
    db.add(r)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"reason_code": "duplicate_receipt", "message": "receipt already submitted for this job_id"},
        )

    node.last_seen_at = datetime.utcnow()
    db.commit()
    return ReceiptResp(accepted=True, server_time=datetime.utcnow().isoformat() + "Z")


def _parse_ts(s: str) -> datetime:
    # Expect RFC3339 with Z
    if s.endswith("Z"):
        s = s[:-1]
    try:
        return datetime.fromisoformat(s)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "invalid_timestamp", "message": "timestamp format is invalid"},
        )


def _get_timeout_ms(params_json: str, default_ms: int) -> int:
    try:
        p = json.loads(params_json)
        v = p.get("timeout_ms")
        return int(v) if v is not None else int(default_ms)
    except Exception:
        return int(default_ms)
