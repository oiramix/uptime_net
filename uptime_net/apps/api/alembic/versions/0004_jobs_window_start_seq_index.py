"""jobs window_start and seq_index

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-24

"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _parse_issued_at(issued_at):
    """Parse issued_at (str from SQLite or datetime) to timezone-aware UTC datetime."""
    if issued_at is None:
        return None
    if hasattr(issued_at, "timestamp"):
        dt = issued_at
    else:
        s = str(issued_at).strip()
        dt = None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
        if dt is None:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(s[:26].rstrip("Z"), fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _floor_window_start_utc(dt, window_s: int = 60) -> datetime:
    """Floor to window start in UTC; returns naive UTC datetime."""
    ts = int(dt.timestamp())
    start = (ts // window_s) * window_s
    return datetime.utcfromtimestamp(start)


def upgrade() -> None:
    conn = op.get_bind()
    dialect_name = conn.dialect.name

    # Idempotent: add columns only if they don't exist
    if dialect_name == "sqlite":
        info = conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
        col_names = [row[1] for row in info]
    else:
        # PostgreSQL: information_schema; others assume columns missing
        try:
            info = conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'jobs' AND column_name IN ('window_start','seq_index')"
            )).fetchall()
            col_names = [row[0] for row in info]
        except Exception:
            col_names = []
    if "window_start" not in col_names:
        op.add_column("jobs", sa.Column("window_start", sa.DateTime(), nullable=True))
    if "seq_index" not in col_names:
        op.add_column("jobs", sa.Column("seq_index", sa.Integer(), nullable=True))

    # Backfill only rows where window_start IS NULL or seq_index IS NULL
    result = conn.execute(text(
        "SELECT job_id, target_id, region_id, check_type, issued_at FROM jobs WHERE window_start IS NULL OR seq_index IS NULL"
    ))
    rows = result.fetchall()

    if rows:
        key_to_seq = defaultdict(int)
        updates = []
        for row in rows:
            job_id, target_id, region_id, check_type, issued_at = row
            if issued_at is None:
                continue
            try:
                dt = _parse_issued_at(issued_at)
            except Exception:
                continue
            if dt is None:
                continue
            window_start = _floor_window_start_utc(dt, 60)
            key = (target_id, region_id, check_type, window_start)
            seq_index = key_to_seq[key]
            key_to_seq[key] += 1
            updates.append((job_id, window_start, seq_index))

        for job_id, window_start, seq_index in updates:
            conn.execute(
                text("UPDATE jobs SET window_start = :ws, seq_index = :si WHERE job_id = :jid"),
                {"ws": window_start, "si": seq_index, "jid": job_id},
            )

    # Create NOT NULL and unique constraint only if constraint does not exist (idempotent)
    if dialect_name == "sqlite":
        existing = conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND name='uq_jobs_window_slot'")).fetchone()
    else:
        try:
            existing = conn.execute(text(
                "SELECT 1 FROM information_schema.table_constraints WHERE table_name = 'jobs' AND constraint_name = 'uq_jobs_window_slot'"
            )).fetchone()
        except Exception:
            existing = None
    if not existing:
        if dialect_name == "sqlite":
            with op.batch_alter_table("jobs") as batch_op:
                batch_op.alter_column(
                    "window_start",
                    existing_type=sa.DateTime(),
                    nullable=False,
                )
                batch_op.alter_column(
                    "seq_index",
                    existing_type=sa.Integer(),
                    nullable=False,
                )
                batch_op.create_unique_constraint(
                    "uq_jobs_window_slot",
                    ["target_id", "region_id", "check_type", "window_start", "seq_index"],
                )
        else:
            op.alter_column(
                "jobs", "window_start",
                existing_type=sa.DateTime(),
                nullable=False,
            )
            op.alter_column(
                "jobs", "seq_index",
                existing_type=sa.Integer(),
                nullable=False,
            )
            op.create_unique_constraint(
                "uq_jobs_window_slot",
                "jobs",
                ["target_id", "region_id", "check_type", "window_start", "seq_index"],
            )


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    if dialect_name == "sqlite":
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.drop_constraint("uq_jobs_window_slot", type_="unique")
            batch_op.drop_column("seq_index")
            batch_op.drop_column("window_start")
    else:
        op.drop_constraint("uq_jobs_window_slot", "jobs", type_="unique")
        op.drop_column("jobs", "seq_index")
        op.drop_column("jobs", "window_start")
