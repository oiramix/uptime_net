from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Dict, List, Tuple

from sqlalchemy import select

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "api"))

from app.core.config import get_settings  # type: ignore  # noqa: E402
from app.core.security import gen_id  # type: ignore  # noqa: E402
from app.db import make_engine, make_session_factory  # type: ignore  # noqa: E402
from app.models import (  # type: ignore  # noqa: E402
    Incident,
    Job,
    Receipt,
    Target,
    VerifiedResult,
)


def floor_window_start(dt: datetime, window_s: int = 60) -> datetime:
    ts = int(dt.timestamp())
    start = (ts // window_s) * window_s
    return datetime.utcfromtimestamp(start)


@dataclass
class ReceiptView:
    target_id: str
    region_id: str
    check_type: str
    window_start: datetime
    ok: bool
    total_ms: int
    ttfb_ms: int | None


def debug_print_recent_receipts(session) -> None:
    # Print the last 10 receipts with joined job/target info
    stmt = (
        select(Receipt, Job, Target)
        .join(Job, Receipt.job_id == Job.job_id)
        .join(Target, Job.target_id == Target.target_id)
        .order_by(Receipt.started_at.desc())
        .limit(10)
    )
    rows = session.execute(stmt).all()
    print("[verifier] last receipts:")
    for receipt, job, target in rows:
        print(
            "[verifier] receipt",
            "job_id=", receipt.job_id,
            "target_id=", job.target_id,
            "region_id=", job.region_id,
            "check_type=", job.check_type,
            "started_at=", receipt.started_at,
            "accepted=", receipt.accepted,
        )


def load_unaggregated_receipts(session) -> List[ReceiptView]:
    # Debug: how many receipts exist at all?
    total_receipts = session.query(Receipt).count()
    print(f"[verifier] total receipts in DB = {total_receipts}")

    # For MVP, aggregate over all receipts (no time filter) to avoid missing data
    stmt = (
        select(Receipt, Job, Target)
        .join(Job, Receipt.job_id == Job.job_id)
        .join(Target, Job.target_id == Target.target_id)
        .where(Receipt.accepted.is_(True))
        .where(Job.check_type == "http")
    )
    joined_rows = session.execute(stmt).all()
    print(f"[verifier] receipts joined with jobs+targets = {len(joined_rows)}")

    out: List[ReceiptView] = []
    for receipt, job, target in joined_rows:
        try:
            payload = json.loads(receipt.receipt_json)
        except json.JSONDecodeError:
            continue
        result = payload.get("result") or {}
        timings = payload.get("timings_ms") or {}
        ok = bool(result.get("ok"))
        total_ms = int(timings.get("total") or 0)
        ttfb_ms = timings.get("ttfb")
        ttfb_val: int | None = int(ttfb_ms) if ttfb_ms is not None else None

        # Window calculation: floor started_at (or finished_at) to 60s window.
        ts_source = receipt.started_at or receipt.finished_at
        window = floor_window_start(ts_source, 60)
        print(
            "[verifier] computed window",
            "job_id=", receipt.job_id,
            "target_id=", job.target_id,
            "region_id=", job.region_id,
            "check_type=", job.check_type,
            "started_at=", receipt.started_at,
            "window_start=", window,
        )

        out.append(
            ReceiptView(
                target_id=job.target_id,
                region_id=job.region_id,
                check_type=job.check_type,
                window_start=window,
                ok=ok,
                total_ms=total_ms,
                ttfb_ms=ttfb_val,
            )
        )
    return out


def ensure_verified_result(session, group_key: Tuple[str, str, str, datetime], views: List[ReceiptView]) -> VerifiedResult | None:
    target_id, region_id, check_type, window_start = group_key

    existing = (
        session.query(VerifiedResult)
        .filter(
            VerifiedResult.target_id == target_id,
            VerifiedResult.region_id == region_id,
            VerifiedResult.check_type == check_type,
            VerifiedResult.window_start == window_start,
        )
        .one_or_none()
    )
    if existing:
        print(
            "[verifier] VerifiedResult already exists for",
            "target_id=", target_id,
            "region_id=", region_id,
            "check_type=", check_type,
            "window_start=", window_start,
        )
        return None

    oks = [v.ok for v in views]
    total_vals = [v.total_ms for v in views if v.total_ms is not None]
    ttfb_vals = [v.ttfb_ms for v in views if v.ttfb_ms is not None]

    if not total_vals:
        return None

    ok_majority = sum(1 for v in oks if v) >= (len(oks) // 2 + 1)
    total_median = int(median(total_vals))
    ttfb_median: int | None = int(median(ttfb_vals)) if ttfb_vals else None

    vr = VerifiedResult(
        verified_result_id=gen_id("vr"),
        target_id=target_id,
        region_id=region_id,
        check_type=check_type,
        window_start=window_start,
        ok=ok_majority,
        total_ms_median=total_median,
        ttfb_ms_median=ttfb_median,
    )
    session.add(vr)
    session.flush()
    print(
        "[verifier] Inserted VerifiedResult for",
        "target_id=", target_id,
        "region_id=", region_id,
        "check_type=", check_type,
        "window_start=", window_start,
        "ok=", ok_majority,
        "total_ms_median=", total_median,
    )
    return vr


def maybe_open_or_close_incident(session, vr: VerifiedResult, target: Target, close_ok_windows: int = 2) -> None:
    # Find previous verified result for this stream
    prev = (
        session.query(VerifiedResult)
        .filter(
            VerifiedResult.target_id == vr.target_id,
            VerifiedResult.region_id == vr.region_id,
            VerifiedResult.check_type == vr.check_type,
            VerifiedResult.window_start < vr.window_start,
        )
        .order_by(VerifiedResult.window_start.desc())
        .first()
    )

    open_incident = (
        session.query(Incident)
        .filter(
            Incident.target_id == vr.target_id,
            Incident.region_id == vr.region_id,
            Incident.check_type == vr.check_type,
            Incident.status == "open",
        )
        .order_by(Incident.opened_at.desc())
        .first()
    )

    # Determine latency spike
    latency_threshold = target.latency_threshold_ms or 2000
    latency_spike = vr.total_ms_median > latency_threshold

    # Open on ok->down or latency spike when previously ok and no open incident
    if open_incident is None:
        if prev and prev.ok and not vr.ok:
            inc = Incident(
                incident_id=gen_id("inc"),
                target_id=vr.target_id,
                region_id=vr.region_id,
                check_type=vr.check_type,
                status="open",
                cause="down",
                opened_at=vr.window_start,
                closed_at=None,
                last_updated_at=vr.window_start,
            )
            session.add(inc)
            return
        if prev and prev.ok and latency_spike and vr.ok:
            inc = Incident(
                incident_id=gen_id("inc"),
                target_id=vr.target_id,
                region_id=vr.region_id,
                check_type=vr.check_type,
                status="open",
                cause="latency_spike",
                opened_at=vr.window_start,
                closed_at=None,
                last_updated_at=vr.window_start,
            )
            session.add(inc)
            return

    # Close when we have N consecutive ok windows
    if open_incident is not None and vr.ok:
        recent = (
            session.query(VerifiedResult)
            .filter(
                VerifiedResult.target_id == vr.target_id,
                VerifiedResult.region_id == vr.region_id,
                VerifiedResult.check_type == vr.check_type,
            )
            .order_by(VerifiedResult.window_start.desc())
            .limit(close_ok_windows)
            .all()
        )
        if len(recent) == close_ok_windows and all(r.ok for r in recent):
            open_incident.status = "closed"
            open_incident.closed_at = vr.window_start
            open_incident.last_updated_at = vr.window_start


def run_once():
    settings = get_settings()
    engine = make_engine(settings.database_url)
    SessionLocal = make_session_factory(engine)

    with SessionLocal() as session:
        debug_print_recent_receipts(session)
        views = load_unaggregated_receipts(session)
        if not views:
            print("No receipts to aggregate.")
            return

        grouped: Dict[Tuple[str, str, str, datetime], List[ReceiptView]] = defaultdict(list)
        for v in views:
            key = (v.target_id, v.region_id, v.check_type, v.window_start)
            grouped[key].append(v)

        print("[verifier] groups to consider:")
        for key, gv in sorted(grouped.items(), key=lambda kv: kv[0][3]):
            target_id, region_id, check_type, window_start = key
            print(
                "[verifier] group",
                "target_id=", target_id,
                "region_id=", region_id,
                "check_type=", check_type,
                "window_start=", window_start,
                "count=", len(gv),
            )

        created = 0
        for key, gv in sorted(grouped.items(), key=lambda kv: kv[0][3]):
            try:
                vr = ensure_verified_result(session, key, gv)
                if vr is None:
                    continue

                target = session.query(Target).filter(Target.target_id == vr.target_id).one()
                maybe_open_or_close_incident(session, vr, target)
                session.commit()
                created += 1
            except Exception as exc:
                session.rollback()
                print("[verifier] ERROR while inserting VerifiedResult for key", key, "exc=", repr(exc))

        print(f"[verifier] Created {created} verified results.")


def main():
    run_once()


if __name__ == "__main__":
    main()

