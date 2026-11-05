"""Unique (assignment_id, student_id) on homework_submissions.

Revision ID: 20261012_0006
Revises: 20261012_0005
Create Date: 2026-10-19

Skips constraint creation when duplicate pairs already exist (report-only path).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0006"
down_revision: Union[str, None] = "20261012_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "homework_submissions" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_unique_constraints("homework_submissions")}
    if "uq_homework_sub_assignment_student" in existing:
        return
    for uc in insp.get_unique_constraints("homework_submissions"):
        cols = list(uc.get("column_names") or [])
        if cols == ["assignment_id", "student_id"]:
            return
    # Detect duplicates — do not force-drop history.
    dupes = bind.execute(
        sa.text(
            """
            SELECT assignment_id, student_id, COUNT(*) AS c
            FROM homework_submissions
            GROUP BY assignment_id, student_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).fetchone()
    if dupes:
        # Leave unconstrained; app-level .first() still applies.
        return
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("homework_submissions") as batch:
            batch.create_unique_constraint(
                "uq_homework_sub_assignment_student",
                ["assignment_id", "student_id"],
            )
    else:
        op.create_unique_constraint(
            "uq_homework_sub_assignment_student",
            "homework_submissions",
            ["assignment_id", "student_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "homework_submissions" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_unique_constraints("homework_submissions")}
    if "uq_homework_sub_assignment_student" not in existing:
        return
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("homework_submissions") as batch:
            batch.drop_constraint("uq_homework_sub_assignment_student", type_="unique")
    else:
        op.drop_constraint(
            "uq_homework_sub_assignment_student",
            "homework_submissions",
            type_="unique",
        )
