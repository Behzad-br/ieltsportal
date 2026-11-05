"""Speaking audio paths + teacher band fields.

Revision ID: 20261012_0005
Revises: 20261012_0004
Create Date: 2026-10-19

Adds durable recording columns on answers and teacher override fields on evaluations.
Idempotent when columns already exist. Uses dialect-safe TEXT (no length modifier).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0005"
down_revision: Union[str, None] = "20261012_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_sql(table: str, column: str, ddl_type: str) -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    if column in existing:
        return
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl_type}"))
    else:
        op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def upgrade() -> None:
    _add_column_sql("answers", "audio_path", "VARCHAR(512)")
    _add_column_sql("answers", "audio_mime", "VARCHAR(128)")
    _add_column_sql("answers", "transcription_error", "TEXT")
    _add_column_sql("answers", "updated_at", "TIMESTAMP")
    _add_column_sql("evaluations", "teacher_band", "FLOAT")
    _add_column_sql("evaluations", "final_band", "FLOAT")
    _add_column_sql("evaluations", "teacher_comments", "TEXT")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table, cols in (
        ("answers", ("updated_at", "transcription_error", "audio_mime", "audio_path")),
        ("evaluations", ("teacher_comments", "final_band", "teacher_band")),
    ):
        if table not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for col in cols:
            if col in existing:
                op.drop_column(table, col)
