"""add reason/status majority to verified_results

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "verified_results",
        sa.Column("reason_code_majority", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "verified_results",
        sa.Column("http_status_majority", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("verified_results", "http_status_majority")
    op.drop_column("verified_results", "reason_code_majority")

