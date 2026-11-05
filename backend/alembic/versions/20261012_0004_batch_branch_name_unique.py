"""Unique (branch_id, name) on auth_batches.

Revision ID: 20261012_0004
Revises: 20261012_0003
Create Date: 2026-10-12

Exact-name uniqueness per campus. Case-insensitive uniqueness remains enforced in app code.
Safe/idempotent when constraint already exists or table is missing.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0004"
down_revision: Union[str, None] = "20261012_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "auth_batches" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_unique_constraints("auth_batches")}
    if "uq_auth_batches_branch_name" in existing:
        return
    # Also skip if an equivalent unnamed unique already covers these columns.
    for uc in insp.get_unique_constraints("auth_batches"):
        cols = uc.get("column_names") or []
        if list(cols) == ["branch_id", "name"]:
            return
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("auth_batches") as batch:
            batch.create_unique_constraint(
                "uq_auth_batches_branch_name",
                ["branch_id", "name"],
            )
    else:
        op.create_unique_constraint(
            "uq_auth_batches_branch_name",
            "auth_batches",
            ["branch_id", "name"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "auth_batches" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_unique_constraints("auth_batches")}
    # create_all may have applied the same UniqueConstraint under a different name.
    target = "uq_auth_batches_branch_name"
    if target not in existing:
        for uc in insp.get_unique_constraints("auth_batches"):
            cols = uc.get("column_names") or []
            if list(cols) == ["branch_id", "name"] and uc.get("name"):
                target = uc["name"]
                break
        else:
            return
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("auth_batches") as batch:
            batch.drop_constraint(target, type_="unique")
    else:
        op.drop_constraint(target, "auth_batches", type_="unique")
