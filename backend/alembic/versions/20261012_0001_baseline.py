"""Baseline: existing Neon / production schema (stamp-only).

Revision ID: 20261012_0001
Revises:
Create Date: 2026-10-12

This revision represents the schema that already exists in the database
(created historically via Base.metadata.create_all + additive ALTER patches).

IMPORTANT:
- upgrade() and downgrade() are intentionally empty.
- Do NOT run this revision as CREATE TABLE against an existing populated DB.
- Existing databases must be stamped: `alembic stamp 20261012_0001`
- Fresh empty databases should use create_all (dev/test) or a future
  bootstrap path, then stamp, then upgrade to head — not replay this
  empty baseline as a full schema creator.

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "20261012_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Empty on purpose — schema already exists; history starts here via stamp.
    pass


def downgrade() -> None:
    # Empty on purpose — baseline must not drop business tables.
    pass
