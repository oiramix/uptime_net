from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Frozen defaults from DECISIONS.md
    k_normal: int = 2
    k_incident: int = 7
    k_escalate: int = 15
    incident_confirm: int = 5  # >=5/7
    escalate_confirm: int = 10  # >=10/15
    job_ttl_seconds: int = 30
    # Timeouts (ms)
    dns_timeout_ms: int = 2000
    tcp_timeout_ms: int = 3000
    tls_timeout_ms: int = 4000
    http_total_timeout_ms: int = 8000

    # Phase 0 region
    default_region_id: str = "global"


def get_settings() -> Settings:
    db = os.environ.get("DATABASE_URL")
    if not db:
        raise RuntimeError("DATABASE_URL env var is required")
    return Settings(database_url=db)
