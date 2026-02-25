from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class Node(Base):
    __tablename__ = "nodes"

    node_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    pubkey_b64: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/quarantined


class Target(Base):
    __tablename__ = "targets"

    target_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    interval_s: Mapped[int] = mapped_column(Integer, default=60)
    check_http: Mapped[bool] = mapped_column(Boolean, default=True)
    check_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    check_dns: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "target_id", "region_id", "check_type", "window_start", "seq_index",
            name="uq_jobs_window_slot",
        ),
    )

    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(32), ForeignKey("targets.target_id"), index=True)
    node_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("nodes.node_id"), nullable=True, index=True)
    region_id: Mapped[str] = mapped_column(String(32), default="global", index=True)
    check_type: Mapped[str] = mapped_column(String(8))  # http|dns|tls
    params_json: Mapped[str] = mapped_column(Text)
    nonce: Mapped[str] = mapped_column(String(64), index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    server_sig_b64: Mapped[str] = mapped_column(String(128))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    seq_index: Mapped[int] = mapped_column(Integer, nullable=False)

    target = relationship("Target")


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_receipt_job"),
    )

    receipt_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("jobs.job_id"), index=True)
    node_id: Mapped[str] = mapped_column(String(32), ForeignKey("nodes.node_id"), index=True)
    nonce: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime] = mapped_column(DateTime)
    receipt_json: Mapped[str] = mapped_column(Text)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job = relationship("Job")


class NodeReputation(Base):
    __tablename__ = "node_reputations"

    node_id: Mapped[str] = mapped_column(String(32), ForeignKey("nodes.node_id"), primary_key=True)
    agree_count: Mapped[int] = mapped_column(Integer, default=0)
    disagree_count: Mapped[int] = mapped_column(Integer, default=0)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PayoutLedger(Base):
    __tablename__ = "payout_ledgers"
    __table_args__ = (UniqueConstraint("node_id", "job_id", name="uq_payout_ledger_node_job"),)

    ledger_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(32), ForeignKey("nodes.node_id"), index=True)
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("jobs.job_id"), index=True)
    target_id: Mapped[str] = mapped_column(String(32), ForeignKey("targets.target_id"), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    units: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VerifiedResult(Base):
    """One per (target_id, region_id, check_type, window_start) after verifier runs."""
    __tablename__ = "verified_results"
    __table_args__ = (
        UniqueConstraint("target_id", "region_id", "check_type", "window_start", name="uq_verified_result_window"),
    )

    verified_result_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(32), ForeignKey("targets.target_id"), index=True)
    region_id: Mapped[str] = mapped_column(String(32), index=True)
    check_type: Mapped[str] = mapped_column(String(8), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
