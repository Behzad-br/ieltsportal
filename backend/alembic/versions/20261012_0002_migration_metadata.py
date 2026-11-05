"""Expand-only: create migration_metadata marker table.

Revision ID: 20261012_0002
Revises: 20261012_0001
Create Date: 2026-10-12

Zero-risk prove-out that Alembic can upgrade/downgrade without touching
business tables or row counts.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0002"
down_revision: Union[str, None] = "20261012_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "migration_metadata",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("key", name="uq_migration_metadata_key"),
    )
    op.execute(
        sa.text(
            "INSERT INTO migration_metadata (key, value) "
            "VALUES ('phase3_alembic', 'established')"
        )
    )


def downgrade() -> None:
    op.drop_table("migration_metadata")
