from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select

# Add shared package to path when running as script
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "packages", "shared"))

from shared.canonical_json import canonical_dumps, strip_keys_deep
from shared.ed25519 import sign_bytes

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "api"))
from app.core.config import get_settings
from app.core.keys import get_or_create_server_sk_b64
from app.core.security import gen_id
from app.db import make_engine, make_session_factory
from app.models import Target, Job


def floor_window_start(dt: datetime, window_s: int = 60) -> datetime:
    ts = int(dt.timestamp())
    start = (ts // window_s) * window_s
    return datetime.utcfromtimestamp(start)


def main():
    settings = get_settings()
    engine = make_engine(settings.database_url)
    SessionLocal = make_session_factory(engine)
    server_sk = get_or_create_server_sk_b64()

    with SessionLocal() as db:
        # Ensure at least one target exists
        t = db.execute(select(Target).limit(1)).scalars().first()
        if not t:
            t = Target(target_id=gen_id("t"), url="https://example.com", interval_s=60, check_http=True, check_tls=True)
            db.add(t)
            db.commit()
            print(f"Created demo target {t.target_id} -> {t.url}")

        now = datetime.utcnow()
        window_start = floor_window_start(now, 60)
        issued_at = now
        expires_at = issued_at + timedelta(seconds=settings.job_ttl_seconds)

        # Create K_normal jobs for each target
        targets = db.execute(select(Target)).scalars().all()
        created = 0
        for target in targets:
            for _ in range(settings.k_normal):
                job_payload = {
                    "job_id": gen_id("j"),
                    "target_id": target.target_id,
                    "check_type": "http",
                    "params": {
                        "url": target.url,
                        "method": "HEAD",
                        "expected_status": [200, 204, 301, 302],
                        "timeout_ms": settings.http_total_timeout_ms,
                    },
                    "nonce": secrets.token_urlsafe(16),
                    "issued_at": issued_at.isoformat() + "Z",
                    "expires_at": expires_at.isoformat() + "Z",
                }
                msg = canonical_dumps(job_payload)
                server_sig = sign_bytes(server_sk, msg)

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
                )
                db.add(job)
                created += 1

        db.commit()
        print(f"Seeded {created} jobs for window_start={window_start.isoformat()}Z")


if __name__ == "__main__":
    main()
