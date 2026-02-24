"""initial

Revision ID: 0001
Revises: 
Create Date: 2026-02-24

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column("node_id", sa.String(length=32), primary_key=True),
        sa.Column("pubkey_b64", sa.String(length=128), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
    )
    op.create_index("ix_nodes_pubkey_b64", "nodes", ["pubkey_b64"], unique=True)
    op.create_index("ix_nodes_token", "nodes", ["token"], unique=True)

    op.create_table(
        "targets",
        sa.Column("target_id", sa.String(length=32), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("interval_s", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("check_http", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("check_tls", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("check_dns", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=32), primary_key=True),
        sa.Column("target_id", sa.String(length=32), sa.ForeignKey("targets.target_id"), nullable=False),
        sa.Column("node_id", sa.String(length=32), sa.ForeignKey("nodes.node_id"), nullable=True),
        sa.Column("region_id", sa.String(length=32), nullable=False, server_default="global"),
        sa.Column("check_type", sa.String(length=8), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("server_sig_b64", sa.String(length=128), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_jobs_target_id", "jobs", ["target_id"], unique=False)
    op.create_index("ix_jobs_node_id", "jobs", ["node_id"], unique=False)
    op.create_index("ix_jobs_region_id", "jobs", ["region_id"], unique=False)
    op.create_index("ix_jobs_nonce", "jobs", ["nonce"], unique=False)

    op.create_table(
        "receipts",
        sa.Column("receipt_id", sa.String(length=32), primary_key=True),
        sa.Column("job_id", sa.String(length=32), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("node_id", sa.String(length=32), sa.ForeignKey("nodes.node_id"), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("receipt_json", sa.Text(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_receipt_job"),
    )
    op.create_index("ix_receipts_job_id", "receipts", ["job_id"], unique=False)
    op.create_index("ix_receipts_node_id", "receipts", ["node_id"], unique=False)


def downgrade() -> None:
    op.drop_table("receipts")
    op.drop_table("jobs")
    op.drop_table("targets")
    op.drop_table("nodes")
