"""verified_results and incidents tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add latency_threshold_ms to targets
    op.add_column(
        "targets",
        sa.Column(
            "latency_threshold_ms",
            sa.Integer(),
            nullable=False,
            server_default="2000",
        ),
    )

    op.create_table(
        "verified_results",
        sa.Column("verified_result_id", sa.String(length=32), primary_key=True),
        sa.Column("target_id", sa.String(length=32), sa.ForeignKey("targets.target_id"), nullable=False),
        sa.Column("region_id", sa.String(length=32), nullable=False),
        sa.Column("check_type", sa.String(length=8), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("total_ms_median", sa.Integer(), nullable=False),
        sa.Column("ttfb_ms_median", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_verified_results_target_region_type_window",
        "verified_results",
        ["target_id", "region_id", "check_type", "window_start"],
        unique=False,
    )

    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.String(length=32), primary_key=True),
        sa.Column("target_id", sa.String(length=32), sa.ForeignKey("targets.target_id"), nullable=False),
        sa.Column("region_id", sa.String(length=32), nullable=False),
        sa.Column("check_type", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("cause", sa.String(length=32), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_incidents_target_status",
        "incidents",
        ["target_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_target_status", table_name="incidents")
    op.drop_table("incidents")

    op.drop_index("ix_verified_results_target_region_type_window", table_name="verified_results")
    op.drop_table("verified_results")

    op.drop_column("targets", "latency_threshold_ms")

