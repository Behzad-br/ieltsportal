"""Expand-only: auth_branches.is_active + updated_at.

Revision ID: 20261012_0003
Revises: 20261012_0002
Create Date: 2026-10-12

Adds soft-deactivation support for campuses without dropping history.
Safe on empty DBs (no auth_branches yet) and idempotent if columns already exist
(e.g. create_all then stamp/upgrade path in tests).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0003"
down_revision: Union[str, None] = "20261012_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "auth_branches" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("auth_branches")}
    if "is_active" not in cols:
        op.add_column(
            "auth_branches",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )
    if "updated_at" not in cols:
        op.add_column(
            "auth_branches",
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "auth_branches" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("auth_branches")}
    if "updated_at" in cols:
        op.drop_column("auth_branches", "updated_at")
    if "is_active" in cols:
        op.drop_column("auth_branches", "is_active")
