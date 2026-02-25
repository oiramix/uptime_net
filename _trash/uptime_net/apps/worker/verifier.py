"""
Verifier: groups accepted receipts by (target_id, region_id, check_type, window_start),
computes majority ok, writes VerifiedResult, updates NodeReputation and PayoutLedger,
and applies quarantine rule.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import gen_id
from app.db import make_engine, make_session_factory
from app.models import (
    Job,
    Node,
    NodeReputation,
    PayoutLedger,
    Receipt,
    VerifiedResult,
)


QUARANTINE_MIN_TOTAL = 20
QUARANTINE_DISAGREE_RATE = 0.30


def run(db: Session) -> int:
    """
    Process accepted receipts: group by (target_id, region_id, check_type, window_start),
    create VerifiedResult per group, update reputation and payout ledger, apply quarantine.
    Returns number of VerifiedResults created.
    """
    # Receipts with accepted=True and their job (for window_start, target_id, region_id, check_type)
    q = (
        select(Receipt, Job)
        .join(Job, Receipt.job_id == Job.job_id)
        .where(Receipt.accepted == True)
    )
    rows = db.execute(q).all()
    if not rows:
        return 0

    # Group by (target_id, region_id, check_type, window_start)
    groups: dict[tuple[str, str, str, datetime], list[tuple[Any, Any]]] = defaultdict(list)
    for receipt, job in rows:
        key = (job.target_id, job.region_id, job.check_type, job.window_start)
        groups[key].append((receipt, job))

    created = 0
    now = datetime.utcnow()
    nodes_updated: set[str] = set()

    for (target_id, region_id, check_type, window_start), receipt_job_list in groups.items():
        # Idempotent: skip if VerifiedResult already exists for this group
        existing = db.query(VerifiedResult).filter(
            VerifiedResult.target_id == target_id,
            VerifiedResult.region_id == region_id,
            VerifiedResult.check_type == check_type,
            VerifiedResult.window_start == window_start,
        ).first()
        if existing:
            continue

        # Parse receipt_json for each to get result.ok
        views = []
        for receipt, job in receipt_job_list:
            try:
                data = json.loads(receipt.receipt_json)
                ok = data.get("result", {}).get("ok", False)
            except Exception:
                ok = False
            views.append((receipt, job, ok))

        # Majority ok
        ok_true = sum(1 for _, _, ok in views if ok)
        ok_majority = ok_true > (len(views) - ok_true)

        # Create VerifiedResult
        vr = VerifiedResult(
            verified_result_id=gen_id("vr"),
            target_id=target_id,
            region_id=region_id,
            check_type=check_type,
            window_start=window_start,
            ok=ok_majority,
            created_at=now,
        )
        db.add(vr)
        created += 1

        # Reputation and payout per receipt
        for receipt, job, ok in views:
            node_id = receipt.node_id
            job_id = receipt.job_id
            rep = db.query(NodeReputation).filter(NodeReputation.node_id == node_id).first()
            if rep is None:
                rep = NodeReputation(
                    node_id=node_id,
                    agree_count=0,
                    disagree_count=0,
                    quarantined_at=None,
                    updated_at=now,
                )
                db.add(rep)

            if ok == ok_majority:
                # Payout ledger: one unit per agreeing receipt (handle duplicate safely)
                try:
                    ledger = PayoutLedger(
                        ledger_id=gen_id("l"),
                        node_id=node_id,
                        job_id=job_id,
                        target_id=job.target_id,
                        window_start=job.window_start,
                        units=1,
                        created_at=now,
                    )
                    db.add(ledger)
                    db.flush()
                except IntegrityError:
                    # (node_id, job_id) already in ledger; skip this receipt for payout/agree
                    continue
                rep.agree_count += 1
                rep.updated_at = now
                nodes_updated.add(node_id)
            else:
                rep.disagree_count += 1
                rep.updated_at = now
                nodes_updated.add(node_id)

        db.flush()

    # Quarantine rule: for each node that was updated, if total >= 20 and disagree_rate >= 0.30
    for node_id in nodes_updated:
        rep = db.query(NodeReputation).filter(NodeReputation.node_id == node_id).first()
        if not rep:
            continue
        total = rep.agree_count + rep.disagree_count
        if total < QUARANTINE_MIN_TOTAL:
            continue
        rate = rep.disagree_count / total if total else 0
        if rate < QUARANTINE_DISAGREE_RATE:
            continue
        node = db.query(Node).filter(Node.node_id == node_id).first()
        if node and node.status != "quarantined":
            node.status = "quarantined"
            if rep.quarantined_at is None:
                rep.quarantined_at = now

    db.commit()
    return created


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    SessionLocal = make_session_factory(engine)
    with SessionLocal() as db:
        n = run(db)
    print("Verifier: created", n, "VerifiedResult(s)")


if __name__ == "__main__":
    main()
