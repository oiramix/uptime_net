"""
Automatic job scheduler: every second, ensures the current 60s window has
K_normal jobs per target (idempotent per target_id, region_id, check_type, window_start),
deletes only expired unclaimed jobs, and signs jobs with the server key.
"""
from __future__ import annotations

print("[scheduler] scheduler.__file__ =", __file__)
import app.models as _m
print("[scheduler] app.models.__file__ =", _m.__file__)
from app.models import Job
print("[scheduler] Job has window_start/seq_index =", hasattr(Job, "window_start"), hasattr(Job, "seq_index"))

import json
import secrets
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from shared.canonical_json import canonical_dumps
from shared.ed25519 import sign_bytes

from app.core.config import get_settings
from app.core.keys import get_or_create_server_sk_b64
from app.core.security import gen_id
from app.db import make_engine, make_session_factory
from app.models import Target


def floor_window_start(dt: datetime, window_s: int = 60) -> datetime:
    ts = int(dt.timestamp())
    start = (ts // window_s) * window_s
    return datetime.utcfromtimestamp(start)


def delete_expired_unclaimed_jobs(db) -> int:
    """Delete jobs that are unclaimed and past expires_at. Returns count deleted."""
    deleted = db.query(Job).filter(
        Job.node_id.is_(None),
        Job.expires_at < datetime.utcnow(),
    ).delete()
    return deleted


def run_tick(settings, SessionLocal, server_sk: str) -> tuple[int, int, datetime | None]:
    """
    One scheduler tick: delete expired unclaimed jobs only (expires_at < now),
    then ensure current 60s window has K_normal jobs per target using explicit
    window_start and seq_index (idempotent). Returns (deleted_count, created_count, window_start).
    """
    now = datetime.utcnow()
    window_start = floor_window_start(now, 60)
    issued_at = now
    expires_at = issued_at + timedelta(seconds=settings.job_ttl_seconds)

    with SessionLocal() as db:
        deleted = delete_expired_unclaimed_jobs(db)
        if deleted:
            db.commit()

        targets = db.execute(select(Target)).scalars().all()
        created = 0
        for target in targets:
            existing = (
                db.query(Job.seq_index)
                .filter(
                    Job.target_id == target.target_id,
                    Job.region_id == settings.default_region_id,
                    Job.check_type == "http",
                    Job.window_start == window_start,
                )
                .all()
            )
            existing_indices = {r[0] for r in existing if r[0] is not None}
            for seq_index in range(settings.k_normal):
                if seq_index in existing_indices:
                    continue
                job_payload = {
                    "job_id": gen_id("j"),
                    "target_id": target.target_id,
                    "check_type": "http",
                    "params": {
                        "url": target.url,
                        "method": "GET",
                        "expected_status": [200, 204, 301, 302],
                        "timeout_ms": settings.http_total_timeout_ms,
                    },
                    "nonce": secrets.token_urlsafe(16),
                    "issued_at": issued_at.isoformat() + "Z",
                    "expires_at": expires_at.isoformat() + "Z",
                }
                msg = canonical_dumps(job_payload)
                server_sig = sign_bytes(server_sk, msg)
                # Required post-migration 0004: window_start and seq_index (NOT NULL in DB)
                job = Job(
                    job_id=job_payload["job_id"],
                    target_id=target.target_id,
                    node_id=None,
                    region_id=settings.default_region_id,
                    check_type="http",
                    params_json=json.dumps(job_payload["params"], separators=(",", ":"), ensure_ascii=False),
                    nonce=job_payload["nonce"],
                    issued_at=issued_at,
                    expires_at=expires_at,
                    server_sig_b64=server_sig,
                    claimed_at=None,
                    window_start=window_start,
                    seq_index=seq_index,
                )
                db.add(job)
                created += 1
        if created or deleted:
            db.commit()
    return deleted, created, window_start


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    SessionLocal = make_session_factory(engine)
    server_sk = get_or_create_server_sk_b64()

    print("Scheduler started (60s windows, K_normal=%d). Ctrl+C to stop." % settings.k_normal)
    while True:
        try:
            deleted, created, window_start = run_tick(settings, SessionLocal, server_sk)
            if deleted > 0 or created > 0:
                parts = []
                if deleted > 0:
                    parts.append("deleted=%d" % deleted)
                if created > 0:
                    parts.append("created=%d" % created)
                ws_str = window_start.isoformat() + "Z" if window_start else ""
                print("[scheduler] window_start=%s" % ws_str, " ".join(parts))
        except Exception as e:
            print("[scheduler] tick error:", e)
        time.sleep(1)


if __name__ == "__main__":
    main()
