"""node_reputation and payout_ledgers

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-24

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    return inspect(conn).has_table(name)


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "verified_results"):
        op.create_table(
            "verified_results",
            sa.Column("verified_result_id", sa.String(length=32), primary_key=True),
            sa.Column("target_id", sa.String(length=32), sa.ForeignKey("targets.target_id"), nullable=False),
            sa.Column("region_id", sa.String(length=32), nullable=False),
            sa.Column("check_type", sa.String(length=8), nullable=False),
            sa.Column("window_start", sa.DateTime(), nullable=False),
            sa.Column("ok", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("target_id", "region_id", "check_type", "window_start", name="uq_verified_result_window"),
        )
        op.create_index("ix_verified_results_target_id", "verified_results", ["target_id"], unique=False)
        op.create_index("ix_verified_results_window_start", "verified_results", ["window_start"], unique=False)
    if not _table_exists(conn, "node_reputations"):
        op.create_table(
            "node_reputations",
            sa.Column("node_id", sa.String(length=32), sa.ForeignKey("nodes.node_id"), primary_key=True),
            sa.Column("agree_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("disagree_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("quarantined_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if not _table_exists(conn, "payout_ledgers"):
        op.create_table(
            "payout_ledgers",
            sa.Column("ledger_id", sa.String(length=32), primary_key=True),
            sa.Column("node_id", sa.String(length=32), sa.ForeignKey("nodes.node_id"), nullable=False),
            sa.Column("job_id", sa.String(length=32), sa.ForeignKey("jobs.job_id"), nullable=False),
            sa.Column("target_id", sa.String(length=32), sa.ForeignKey("targets.target_id"), nullable=False),
            sa.Column("window_start", sa.DateTime(), nullable=False),
            sa.Column("units", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("node_id", "job_id", name="uq_payout_ledger_node_job"),
        )
        op.create_index("ix_payout_ledgers_node_id", "payout_ledgers", ["node_id"], unique=False)
        op.create_index("ix_payout_ledgers_job_id", "payout_ledgers", ["job_id"], unique=False)
        op.create_index("ix_payout_ledgers_target_id", "payout_ledgers", ["target_id"], unique=False)
        op.create_index("ix_payout_ledgers_window_start", "payout_ledgers", ["window_start"], unique=False)


def downgrade() -> None:
    op.drop_table("payout_ledgers")
    op.drop_table("node_reputations")
    op.drop_table("verified_results")
