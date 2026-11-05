"""
Canonical identity resolution for Phase 2.

Production identity is always auth_users.id (JWT sub).
Legacy identifiers may be resolved for READ compatibility only when unambiguous.
Never silently map "local", "s1", or demo-* to a real user.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.models import User
from app.config import settings

_logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_BLOCKED_LEGACY = frozenset(
    {
        "local",
        "s1",
        "teacher",
        "demo-teacher",
        "demo-teacher-1",
        "demo-student",
        "demo-student-1",
    }
)


def is_blocked_legacy_id(raw: str) -> bool:
    key = (raw or "").strip().lower()
    if not key:
        return True
    if key in _BLOCKED_LEGACY:
        return True
    if key.startswith("demo-"):
        return True
    if key == (settings.DEFAULT_STUDENT_ID or "").strip().lower():
        return True
    return False


def resolve_canonical_user_id(
    db: Session,
    raw_id: Optional[str],
    *,
    actor: Optional[User] = None,
    expected_role: Optional[str] = None,
    strict: bool = True,
) -> Optional[str]:
    """
    Resolve a raw identity string to auth_users.id.

    Rules:
    1. Exact auth_users.id match → return it (optionally role-filtered).
    2. Exact unique email/username match → return that id (logged as legacy).
    3. Blocked legacy (local/s1/demo-*) → never auto-map.
    4. Ambiguous / unknown → None (strict=False) or HTTP 400/404 (strict=True).
    """
    raw = (raw_id or "").strip()
    if not raw:
        if strict:
            raise HTTPException(400, "Student id required.")
        return None

    if is_blocked_legacy_id(raw):
        _logger.warning(
            "identity_blocked_legacy raw=%s actor=%s",
            raw,
            getattr(actor, "id", None),
        )
        if strict:
            raise HTTPException(
                400,
                f"Legacy identity '{raw}' is not valid in staging. Use a canonical auth_users.id.",
            )
        return None

    by_id = db.query(User).filter_by(id=raw).first()
    if by_id:
        if expected_role and by_id.role != expected_role:
            if strict:
                raise HTTPException(400, f"User {raw} is not a {expected_role}.")
            return None
        return by_id.id

    key = raw.lower()
    matches = (
        db.query(User)
        .filter((User.email == key) | (User.username == key))
        .all()
    )
    if expected_role:
        matches = [u for u in matches if u.role == expected_role]

    if len(matches) == 1:
        _logger.info(
            "identity_legacy_fallback raw=%s resolved=%s actor=%s",
            raw,
            matches[0].id,
            getattr(actor, "id", None),
        )
        return matches[0].id

    if len(matches) > 1:
        _logger.warning("identity_ambiguous raw=%s count=%s", raw, len(matches))
        if strict:
            raise HTTPException(400, f"Identity '{raw}' is ambiguous.")
        return None

    if strict:
        raise HTTPException(404, f"User '{raw}' not found.")
    return None


def assert_staff_may_impersonate_student(actor: User, target: User) -> None:
    """Branch/teacher scoping for X-Student-Id impersonation."""
    if target.role != "student":
        raise HTTPException(400, "Impersonation target must be a student.")
    if not target.is_active:
        raise HTTPException(403, "Target student account is inactive.")

    if actor.role == "super_admin":
        return

    if actor.role == "branch_admin":
        if not actor.branch_id or target.branch_id != actor.branch_id:
            raise HTTPException(403, "Cannot target a student outside your branch.")
        return

    if actor.role == "teacher":
        # Phase 2: same-branch minimum. Batch membership hardening is Phase 5/6.
        if not actor.branch_id or target.branch_id != actor.branch_id:
            raise HTTPException(403, "Cannot target a student outside your branch.")
        return

    raise HTTPException(403, "Not allowed to impersonate students.")
