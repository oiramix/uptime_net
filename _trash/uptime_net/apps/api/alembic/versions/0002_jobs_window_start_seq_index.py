"""jobs window_start and seq_index

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-24

"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _floor_window_start(dt: datetime, window_s: int = 60) -> datetime:
    ts = int(dt.timestamp())
    start = (ts // window_s) * window_s
    return datetime.utcfromtimestamp(start)


def upgrade() -> None:
    op.add_column("jobs", sa.Column("window_start", sa.DateTime(), nullable=True))
    op.add_column("jobs", sa.Column("seq_index", sa.Integer(), nullable=True))

    conn = op.get_bind()
    result = conn.execute(text("SELECT job_id, target_id, region_id, check_type, issued_at FROM jobs"))
    rows = result.fetchall()

    if rows:
        key_to_seq = defaultdict(int)
        updates = []
        for row in rows:
            job_id, target_id, region_id, check_type, issued_at = row
            if issued_at is None:
                continue
            window_start = _floor_window_start(issued_at, 60)
            key = (target_id, region_id, check_type, window_start)
            seq_index = key_to_seq[key]
            key_to_seq[key] += 1
            updates.append((job_id, window_start, seq_index))

        for job_id, window_start, seq_index in updates:
            conn.execute(
                text("UPDATE jobs SET window_start = :ws, seq_index = :si WHERE job_id = :jid"),
                {"ws": window_start, "si": seq_index, "jid": job_id},
            )

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
    op.drop_constraint("uq_jobs_window_slot", "jobs", type_="unique")
    op.drop_column("jobs", "seq_index")
    op.drop_column("jobs", "window_start")
