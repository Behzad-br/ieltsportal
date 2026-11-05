from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.identity import (
    assert_staff_may_impersonate_student,
    is_blocked_legacy_id,
    resolve_canonical_user_id,
)
from app.auth.models import User
from app.auth.security import decode_access_token
from app.config import settings
from app.database import get_db

_bearer = HTTPBearer(auto_error=False)

# Matches frontend `demo-mode.ts` DEMO_TOKEN — local SPA sandbox only.
_DEMO_ACCESS_TOKEN = "demo-local-token"


def _is_demo_bearer(creds: Optional[HTTPAuthorizationCredentials]) -> bool:
    return bool(creds and creds.credentials == _DEMO_ACCESS_TOKEN)


def _user_from_token(db: Session, token: str) -> User:
    """
    Resolve JWT → auth_users row.

    invalid/expired/unknown sub → 401
    inactive user → 403
    """
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired token.")
    user_id = str(payload.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(401, "Invalid token: missing subject.")
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(401, "Unknown user.")
    if not user.is_active:
        raise HTTPException(403, "This account is disabled.")
    from app.auth.access import assert_user_campus_active

    assert_user_campus_active(db, user)
    return user


def get_current_user(
    db: Session = Depends(get_db),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> User:
    if not creds or not creds.credentials:
        raise HTTPException(401, "Authentication required.")
    if _is_demo_bearer(creds):
        raise HTTPException(401, "Demo token is not valid for authenticated API access.")
    return _user_from_token(db, creds.credentials)


def get_optional_user(
    db: Session = Depends(get_db),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[User]:
    if not creds or not creds.credentials:
        return None
    if _is_demo_bearer(creds):
        return None
    try:
        return _user_from_token(db, creds.credentials)
    except HTTPException as exc:
        # Propagate inactive (403); treat other auth failures as anonymous.
        if exc.status_code == 403:
            raise
        return None


def require_roles(*roles: str):
    allowed = set(roles)

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(403, "Not allowed for this role.")
        return user

    return _dep


def _legacy_or_demo_student_id(
    x_student_id: Optional[str],
    creds: Optional[HTTPAuthorizationCredentials],
) -> str:
    """Only for DEMO bearer or AUTH_LEGACY_HEADERS=True (tests / explicit sandbox)."""
    return ((x_student_id or "").strip() or settings.DEFAULT_STUDENT_ID)[:64]


def resolve_student_id(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_student_id: Optional[str] = Header(default=None, alias="X-Student-Id"),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    """
    Canonical student id for skill/homework/notes writes.

    STUDENT JWT: always own auth_users.id (header ignored).
    STAFF JWT + X-Student-Id: must resolve to real student UUID; branch-scoped.
    Demo / AUTH_LEGACY_HEADERS: header or DEFAULT_STUDENT_ID (sandbox/tests only).
    """
    if user and user.role == "student":
        # Header must never override a student JWT.
        return user.id

    if user and user.role in {"teacher", "branch_admin", "super_admin"}:
        raw = (x_student_id or "").strip()
        if not raw:
            raise HTTPException(400, "X-Student-Id required for staff acting as a student.")
        if is_blocked_legacy_id(raw) and not settings.AUTH_LEGACY_HEADERS:
            raise HTTPException(400, f"Legacy identity '{raw}' is not allowed.")
        canonical = resolve_canonical_user_id(
            db, raw, actor=user, expected_role="student", strict=True
        )
        assert canonical is not None
        target = db.query(User).filter_by(id=canonical).first()
        assert target is not None
        assert_staff_may_impersonate_student(user, target)
        return target.id

    if _is_demo_bearer(creds) or settings.AUTH_LEGACY_HEADERS:
        return _legacy_or_demo_student_id(x_student_id, creds)

    raise HTTPException(401, "Student authentication required.")


def resolve_teacher_id(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_teacher_id: Optional[str] = Header(default=None, alias="X-Teacher-Id"),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    if user and user.role == "teacher":
        return user.id

    if user and user.role in {"super_admin", "branch_admin"}:
        raw = (x_teacher_id or "").strip()
        if not raw or raw == user.id:
            return user.id
        if is_blocked_legacy_id(raw) and not settings.AUTH_LEGACY_HEADERS:
            raise HTTPException(400, f"Legacy teacher identity '{raw}' is not allowed.")
        canonical = resolve_canonical_user_id(
            db, raw, actor=user, expected_role="teacher", strict=True
        )
        assert canonical is not None
        return canonical

    if _is_demo_bearer(creds):
        return ((x_teacher_id or "").strip() or "demo-teacher")[:64]
    if settings.AUTH_LEGACY_HEADERS:
        return ((x_teacher_id or "").strip() or "teacher")[:64]

    raise HTTPException(401, "Teacher authentication required.")


def resolve_student_keys(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_student_id: Optional[str] = Header(default=None, alias="X-Student-Id"),
    x_student_aliases: Optional[str] = Header(default=None, alias="X-Student-Aliases"),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> list[str]:
    """
    Keys for historical READ matching (mocks inbox, homework visibility).

    Staging/JWT student: canonical id + DB username/email only.
    Client X-Student-Aliases are ignored for student JWT (prevents spoofing).
    Legacy/demo: header id + optional client aliases (sandbox/tests).
    """
    keys: list[str] = []

    if user and user.role == "student":
        keys.append(user.id)
        if user.username:
            keys.append(user.username)
        if user.email:
            keys.append(user.email)
        # Do NOT merge client-supplied aliases for authenticated students.
    elif user and user.role in {"teacher", "branch_admin", "super_admin"}:
        raw = (x_student_id or "").strip()
        if not raw:
            # Staff routes that also Depends(get_student_keys) without impersonation
            # (e.g. LiveKit teacher token) — use actor id only, never invent local/s1.
            keys.append(user.id)
        else:
            sid = resolve_student_id(
                db=db, user=user, x_student_id=x_student_id, creds=creds
            )
            keys.append(sid)
            target = db.query(User).filter_by(id=sid).first()
            if target:
                if target.username:
                    keys.append(target.username)
                if target.email:
                    keys.append(target.email)
    elif _is_demo_bearer(creds) or settings.AUTH_LEGACY_HEADERS:
        sid = _legacy_or_demo_student_id(x_student_id, creds)
        keys.append(sid)
        if x_student_aliases:
            keys.extend([p.strip() for p in x_student_aliases.split(",") if p.strip()])
    else:
        raise HTTPException(401, "Student authentication required.")

    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    if not out:
        raise HTTPException(401, "Student authentication required.")
    return out


# Back-compat aliases used across routers
get_student_id = resolve_student_id
get_teacher_id = resolve_teacher_id
get_student_keys = resolve_student_keys
