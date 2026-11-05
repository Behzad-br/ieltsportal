"""Unique (assignment_id, student_id) on mock_assignment_students.

Revision ID: 20261012_0007
Revises: 20261012_0006
Create Date: 2026-10-19

Skips constraint creation when duplicate pairs already exist (report-only path).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0007"
down_revision: Union[str, None] = "20261012_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "mock_assignment_students" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_unique_constraints("mock_assignment_students")}
    if "uq_mock_assignment_students_assignment_student" in existing:
        return
    for uc in insp.get_unique_constraints("mock_assignment_students"):
        cols = list(uc.get("column_names") or [])
        if cols == ["assignment_id", "student_id"]:
            return
    dupes = bind.execute(
        sa.text(
            """
            SELECT assignment_id, student_id, COUNT(*) AS c
            FROM mock_assignment_students
            GROUP BY assignment_id, student_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).fetchone()
    if dupes:
        return
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("mock_assignment_students") as batch:
            batch.create_unique_constraint(
                "uq_mock_assignment_students_assignment_student",
                ["assignment_id", "student_id"],
            )
    else:
        op.create_unique_constraint(
            "uq_mock_assignment_students_assignment_student",
            "mock_assignment_students",
            ["assignment_id", "student_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "mock_assignment_students" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_unique_constraints("mock_assignment_students")}
    if "uq_mock_assignment_students_assignment_student" not in existing:
        return
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("mock_assignment_students") as batch:
            batch.drop_constraint(
                "uq_mock_assignment_students_assignment_student",
                type_="unique",
            )
    else:
        op.drop_constraint(
            "uq_mock_assignment_students_assignment_student",
            "mock_assignment_students",
            type_="unique",
        )
