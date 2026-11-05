from __future__ import annotations

import re
import uuid
from typing import Optional

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.access import (
    assert_batch_name_available,
    assert_batch_on_branch,
    assert_branch_assignable,
    assert_can_access_batch,
    assert_can_manage_user,
    assert_can_view_user,
    assert_same_branch,
    assert_user_campus_active,
    assign_batch_teacher,
    clear_teacher_batch_links,
    count_students_in_batch,
    count_unassigned_students,
    rename_batch_cascade,
    resolve_create_branch_id,
    resolve_list_branch_scope,
    sync_teacher_batch_links,
)
from app.auth.deps import get_current_user, require_roles
from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.security_hardening import client_ip, login_limiter, rate_limit_or_429

router = APIRouter(prefix="/auth", tags=["auth"])
org_router = APIRouter(prefix="/org", tags=["org"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=6)
    role: Optional[str] = None  # optional hint: student|teacher|branch_admin|super_admin


class UserCreate(BaseModel):
    role: str
    email: str
    username: str
    password: str = Field(min_length=6)
    full_name: str = ""
    branch_id: str = ""
    batch: str = ""
    batches: str = ""


class UserPatch(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=6)
    full_name: Optional[str] = None
    branch_id: Optional[str] = None
    batch: Optional[str] = None
    batches: Optional[str] = None
    is_active: Optional[bool] = None


class BranchAdminBody(BaseModel):
    name: str = ""
    username: str = Field(min_length=3)
    password: str = Field(default="", min_length=0)
    email: Optional[str] = None


class BranchBody(BaseModel):
    name: str
    city: str = ""
    image_url: str = ""
    id: Optional[str] = None
    admins: list[BranchAdminBody] = Field(default_factory=list)


class BranchStatusBody(BaseModel):
    is_active: bool


class BatchBody(BaseModel):
    name: str
    schedule: str = ""
    subjects: str = ""
    branch_id: str = ""
    teacher_id: str = ""
    id: Optional[str] = None


class BatchPatch(BaseModel):
    name: Optional[str] = None
    schedule: Optional[str] = None
    subjects: Optional[str] = None
    teacher_id: Optional[str] = None


def _slug(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or f"id-{uuid.uuid4().hex[:8]}"
    return base[:48]


def _public_user(u: User) -> dict:
    return {
        "id": u.id,
        "role": u.role,
        "email": u.email,
        "username": u.username,
        "full_name": u.full_name or u.username,
        "name": u.full_name or u.username,
        "branch_id": u.branch_id or "",
        "branchId": u.branch_id or "",
        "batch": u.batch or "",
        "batches": u.batches or "",
        "is_active": bool(u.is_active),
    }


def _branch_admin_email(username: str, email: Optional[str], branch_id: str) -> str:
    raw = (email or "").strip().lower()
    if raw:
        return raw
    return f"{username.strip().lower()}@{_slug(branch_id) or 'branch'}.fti.local"


def _normalize_branch_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise HTTPException(400, "Branch name is required.")
    if len(name) > 120:
        raise HTTPException(400, "Branch name must be at most 120 characters.")
    return name


def _assert_unique_branch_name(db: Session, name: str, *, exclude_id: Optional[str] = None) -> None:
    q = db.query(Branch).filter(Branch.name == name)
    if exclude_id:
        q = q.filter(Branch.id != exclude_id)
    if q.first():
        raise HTTPException(409, "A branch with this name already exists.")


def _public_branch(db: Session, b: Branch) -> dict:
    teachers = db.query(User).filter_by(role="teacher", branch_id=b.id, is_active=True).count()
    students = db.query(User).filter_by(role="student", branch_id=b.id, is_active=True).count()
    batches = db.query(Batch).filter_by(branch_id=b.id).count()
    admins = (
        db.query(User)
        .filter_by(role="branch_admin", branch_id=b.id, is_active=True)
        .order_by(User.full_name.asc())
        .all()
    )
    is_active = bool(getattr(b, "is_active", True))
    return {
        "id": b.id,
        "name": b.name,
        "city": b.city or "",
        "imageUrl": b.image_url or "",
        "is_active": is_active,
        "isActive": is_active,
        "teachers": teachers,
        "students": students,
        "batches": batches,
        # Band average is computed by /api/analytics/* — never invent 0.0 here.
        "avg": None,
        "admins": [{"name": a.full_name, "username": a.username, "password": ""} for a in admins],
        "admin": admins[0].full_name if admins else "",
        "adminUsername": admins[0].username if admins else "",
        "adminPassword": "",
        "created_at": b.created_at.isoformat() if getattr(b, "created_at", None) else None,
        "updated_at": b.updated_at.isoformat() if getattr(b, "updated_at", None) else None,
    }


def _get_branch_or_404(db: Session, branch_id: str) -> Branch:
    row = db.query(Branch).filter_by(id=branch_id).first()
    if not row:
        raise HTTPException(404, "Branch not found.")
    return row


def _assert_unique_login(db: Session, *, email: str, username: str, exclude_user_id: Optional[str] = None) -> None:
    q = db.query(User).filter((User.email == email) | (User.username == username))
    if exclude_user_id:
        q = q.filter(User.id != exclude_user_id)
    if q.first():
        raise HTTPException(409, "Email or username already exists.")


def _create_branch_admin(
    db: Session,
    *,
    branch_id: str,
    admin: BranchAdminBody,
    require_password: bool,
) -> User:
    """Nested BA create (branch create/update). Password hashed; never returned in API bodies."""
    username = admin.username.strip().lower()
    if len(username) < 3:
        raise HTTPException(400, "Admin username must be at least 3 characters.")
    password = (admin.password or "").strip()
    if require_password and len(password) < 6:
        raise HTTPException(400, f"Password for @{username} must be at least 6 characters.")
    if password and len(password) < 6:
        raise HTTPException(400, f"Password for @{username} must be at least 6 characters.")
    email = _branch_admin_email(username, admin.email, branch_id)
    _assert_unique_login(db, email=email, username=username)
    row = User(
        id=str(uuid.uuid4()),
        role="branch_admin",
        email=email,
        username=username,
        password_hash=hash_password(password),
        full_name=(admin.name or username).strip() or username,
        branch_id=branch_id,
        is_active=True,
    )
    db.add(row)
    return row


def _find_login(db: Session, key: str, role: Optional[str]) -> Optional[User]:
    key = key.strip().lower()
    q = db.query(User)
    if role:
        role_map = {
            "student": "student",
            "teacher": "teacher",
            "branch-admin": "branch_admin",
            "branch_admin": "branch_admin",
            "admin": "super_admin",
            "super_admin": "super_admin",
            "super-admin": "super_admin",
        }
        mapped = role_map.get(role.strip().lower())
        if mapped:
            q = q.filter(User.role == mapped)
    return q.filter((User.email == key) | (User.username == key)).first()


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    rate_limit_or_429(login_limiter, f"login:{client_ip(request)}")
    user = _find_login(db, body.username, body.role)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong username/email or password.")
    if not user.is_active:
        raise HTTPException(403, "This account is disabled.")
    assert_user_campus_active(db, user)
    token = create_access_token(sub=user.id, role=user.role, extra={"branch_id": user.branch_id or ""})
    return {"access_token": token, "token_type": "bearer", "user": _public_user(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _public_user(user)


@router.post("/change-password")
def change_password(
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current = str(body.get("current_password") or "")
    new = str(body.get("new_password") or "")
    if len(new) < 6:
        raise HTTPException(400, "New password must be at least 6 characters.")
    if not verify_password(current, user.password_hash):
        raise HTTPException(400, "Current password is wrong.")
    user.password_hash = hash_password(new)
    db.commit()
    return {"ok": True}


@org_router.get("/users")
def list_users(
    role: Optional[str] = None,
    branch_id: Optional[str] = None,
    user: User = Depends(require_roles("super_admin", "branch_admin", "teacher")),
    db: Session = Depends(get_db),
):
    q = db.query(User)
    if role:
        q = q.filter(User.role == role)
    if user.role == "branch_admin":
        # Campus-scoped teachers/students only — never a branch-admin directory.
        q = q.filter(User.branch_id == user.branch_id, User.role.in_(("teacher", "student")))
        if role == "branch_admin":
            raise HTTPException(403, "Not allowed.")
    elif user.role == "teacher":
        q = q.filter(User.role == "student", User.branch_id == user.branch_id)
    elif branch_id:
        q = q.filter(User.branch_id == branch_id)
    rows = q.order_by(User.full_name.asc()).all()
    return {"users": [_public_user(u) for u in rows]}


@org_router.get("/users/{user_id}")
def get_user(
    user_id: str,
    actor: User = Depends(require_roles("super_admin", "branch_admin", "teacher", "student")),
    db: Session = Depends(get_db),
):
    row = db.query(User).filter_by(id=user_id).first()
    if not row:
        raise HTTPException(404, "User not found.")
    assert_can_view_user(actor, row)
    return _public_user(row)


@org_router.post("/users")
def create_user(
    body: UserCreate,
    actor: User = Depends(require_roles("super_admin", "branch_admin", "teacher")),
    db: Session = Depends(get_db),
):
    role = body.role.strip().lower().replace("-", "_")
    if role not in {"super_admin", "branch_admin", "teacher", "student"}:
        raise HTTPException(400, "Invalid role.")
    if actor.role == "teacher" and role != "student":
        raise HTTPException(403, "Teachers can only create students.")
    if actor.role == "branch_admin" and role not in {"teacher", "student"}:
        raise HTTPException(403, "Branch admins may only create teachers and students.")
    if actor.role != "super_admin" and role == "super_admin":
        raise HTTPException(403, "Not allowed.")
    # Only Super Admin may create Branch Admins (standalone path).
    if role == "branch_admin" and actor.role != "super_admin":
        raise HTTPException(403, "Only Super Admin can create branch admins.")

    email = body.email.strip().lower()
    username = body.username.strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "A valid email is required.")
    if len(username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters.")
    if db.query(User).filter((User.email == email) | (User.username == username)).first():
        raise HTTPException(409, "Email or username already exists.")

    branch_id = resolve_create_branch_id(actor, body.branch_id)
    if role == "super_admin":
        branch_id = ""
    elif role == "branch_admin":
        branch_id = assert_branch_assignable(db, branch_id, require_active=True)
    else:
        # Teachers/students must land on a real active campus.
        branch_id = assert_branch_assignable(db, branch_id, require_active=True)

    # Students use auth_users.batch (singular label). Teacher CSV stays on batches.
    student_batch = ""
    teacher_batches = body.batches or ""
    if role == "student":
        student_batch = assert_batch_on_branch(db, branch_id, body.batch, allow_empty=True)
        teacher_batches = ""
    elif role == "teacher":
        student_batch = ""

    row = User(
        id=str(uuid.uuid4()),
        role=role,
        email=email,
        username=username,
        password_hash=hash_password(body.password),
        full_name=(body.full_name or username).strip(),
        branch_id=branch_id,
        batch=student_batch,
        batches=teacher_batches,
        is_active=True,
    )
    db.add(row)
    if role == "teacher" and (teacher_batches or "").strip():
        sync_teacher_batch_links(db, row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email or username already exists.")
    db.refresh(row)
    return _public_user(row)


@org_router.patch("/users/{user_id}")
def patch_user(
    user_id: str,
    body: UserPatch,
    actor: User = Depends(require_roles("super_admin", "branch_admin", "teacher", "student")),
    db: Session = Depends(get_db),
):
    row = db.query(User).filter_by(id=user_id).first()
    if not row:
        raise HTTPException(404, "User not found.")

    # Student self-service: safe profile fields only (no branch/batch/status escalation).
    if actor.role == "student":
        if actor.id != row.id:
            raise HTTPException(403, "Not allowed.")
        if any(
            v is not None
            for v in (body.branch_id, body.batch, body.batches, body.is_active)
        ):
            raise HTTPException(403, "Students cannot change branch, batch, or account status.")
        if body.email is not None:
            email = body.email.strip().lower()
            if not email or "@" not in email:
                raise HTTPException(400, "A valid email is required.")
            _assert_unique_login(db, email=email, username=row.username, exclude_user_id=row.id)
            row.email = email
        if body.username is not None:
            username = body.username.strip().lower()
            if len(username) < 3:
                raise HTTPException(400, "Username must be at least 3 characters.")
            _assert_unique_login(db, email=row.email, username=username, exclude_user_id=row.id)
            row.username = username
        if body.full_name is not None:
            row.full_name = body.full_name.strip()
        if body.password:
            if len(body.password.strip()) < 6:
                raise HTTPException(400, "Password must be at least 6 characters.")
            row.password_hash = hash_password(body.password)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Email or username already exists.")
        db.refresh(row)
        return _public_user(row)

    assert_can_manage_user(actor, row)

    # Role is immutable via this endpoint (no mass-assignment field on UserPatch).
    if body.email is not None:
        email = body.email.strip().lower()
        if not email or "@" not in email:
            raise HTTPException(400, "A valid email is required.")
        _assert_unique_login(db, email=email, username=row.username, exclude_user_id=row.id)
        row.email = email
    if body.username is not None:
        username = body.username.strip().lower()
        if len(username) < 3:
            raise HTTPException(400, "Username must be at least 3 characters.")
        _assert_unique_login(db, email=row.email, username=username, exclude_user_id=row.id)
        row.username = username
    if body.full_name is not None:
        row.full_name = body.full_name.strip()

    if body.branch_id is not None:
        if actor.role != "super_admin":
            raise HTTPException(403, "Only Super Admin can reassign branches.")
        # Explicit SA reassignment: destination active; history stays; batch/teacher links cleared.
        if row.role == "super_admin":
            row.branch_id = ""
        else:
            new_bid = assert_branch_assignable(db, body.branch_id, require_active=True)
            if row.role == "teacher" and new_bid != (row.branch_id or ""):
                clear_teacher_batch_links(db, row)
            if row.role == "student" and new_bid != (row.branch_id or ""):
                row.batch = ""
            row.branch_id = new_bid

    if body.batch is not None:
        if row.role != "student":
            raise HTTPException(400, "batch applies only to student accounts.")
        if actor.role not in {"super_admin", "branch_admin", "teacher"}:
            raise HTTPException(403, "Not allowed.")
        if actor.role in {"branch_admin", "teacher"}:
            assert_same_branch(actor, row.branch_id)
        row.batch = assert_batch_on_branch(db, row.branch_id, body.batch, allow_empty=True)

    if body.batches is not None:
        if row.role == "teacher":
            if actor.role not in {"super_admin", "branch_admin"}:
                raise HTTPException(403, "Only Super Admin or Branch Admin can change teacher batch assignments.")
            if actor.role == "branch_admin":
                assert_same_branch(actor, row.branch_id)
            row.batches = body.batches
            sync_teacher_batch_links(db, row)
        elif row.role == "student":
            # Students do not use the teacher CSV field.
            raise HTTPException(400, "Use 'batch' for student batch assignment.")
        else:
            row.batches = body.batches

    if body.is_active is not None:
        if row.id == actor.id:
            raise HTTPException(400, "Cannot change your own active status.")
        if actor.role == "super_admin":
            row.is_active = body.is_active
        elif actor.role == "branch_admin" and row.role in {"teacher", "student"}:
            row.is_active = body.is_active
        else:
            raise HTTPException(403, "Not allowed.")

    if body.password:
        if len(body.password.strip()) < 6:
            raise HTTPException(400, "Password must be at least 6 characters.")
        row.password_hash = hash_password(body.password)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email or username already exists.")
    db.refresh(row)
    return _public_user(row)


@org_router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    actor: User = Depends(require_roles("super_admin", "branch_admin", "teacher")),
    db: Session = Depends(get_db),
):
    row = db.query(User).filter_by(id=user_id).first()
    if not row:
        raise HTTPException(404, "User not found.")
    if row.id == actor.id:
        raise HTTPException(400, "Cannot delete your own account.")
    assert_can_manage_user(actor, row)
    # Soft-deactivate only — retain history. Zero active BAs on a campus is allowed;
    # Super Admin can always add another via branch edit or POST /org/users.
    row.is_active = False
    db.commit()
    return {"ok": True, "policy": "soft_deactivate"}


@org_router.get("/branches")
def list_branches(
    active_only: bool = False,
    user: User = Depends(require_roles("super_admin", "branch_admin", "teacher", "student")),
    db: Session = Depends(get_db),
):
    q = db.query(Branch)
    if user.role != "super_admin":
        scope = resolve_list_branch_scope(user, None)
        q = q.filter(Branch.id == scope)
    elif active_only:
        q = q.filter(Branch.is_active.is_(True))
    rows = q.order_by(Branch.name.asc()).all()
    return {"branches": [_public_branch(db, b) for b in rows]}


@org_router.get("/branches/{branch_id}")
def get_branch(
    branch_id: str,
    user: User = Depends(require_roles("super_admin", "branch_admin", "teacher", "student")),
    db: Session = Depends(get_db),
):
    row = _get_branch_or_404(db, branch_id)
    if user.role != "super_admin":
        assert_same_branch(user, row.id)
    return _public_branch(db, row)


@org_router.post("/branches")
def create_branch(
    body: BranchBody,
    _: User = Depends(require_roles("super_admin")),
    db: Session = Depends(get_db),
):
    if not body.admins:
        raise HTTPException(400, "At least one branch admin is required.")
    name = _normalize_branch_name(body.name)
    city = (body.city or "").strip()
    if len(city) > 80:
        raise HTTPException(400, "City must be at most 80 characters.")
    bid = (body.id or _slug(name)).strip()
    if not bid or len(bid) > 64:
        raise HTTPException(400, "Invalid branch id.")
    if db.query(Branch).filter_by(id=bid).first():
        raise HTTPException(409, "Branch id already exists.")
    _assert_unique_branch_name(db, name)

    # Pre-check duplicate usernames inside the request itself.
    seen: set[str] = set()
    for admin in body.admins:
        key = admin.username.strip().lower()
        if key in seen:
            raise HTTPException(400, f"Username @{key} is used twice on this campus.")
        seen.add(key)

    now = datetime.utcnow()
    row = Branch(
        id=bid,
        name=name,
        city=city,
        image_url=body.image_url or "",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        for admin in body.admins:
            _create_branch_admin(db, branch_id=bid, admin=admin, require_password=True)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Branch id or name already exists.")
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    return _public_branch(db, row)


@org_router.put("/branches/{branch_id}")
def update_branch(
    branch_id: str,
    body: BranchBody,
    _: User = Depends(require_roles("super_admin")),
    db: Session = Depends(get_db),
):
    row = _get_branch_or_404(db, branch_id)
    if not body.admins:
        raise HTTPException(400, "At least one branch admin is required.")
    name = _normalize_branch_name(body.name)
    city = (body.city or "").strip()
    if len(city) > 80:
        raise HTTPException(400, "City must be at most 80 characters.")
    _assert_unique_branch_name(db, name, exclude_id=branch_id)

    seen: set[str] = set()
    for admin in body.admins:
        key = admin.username.strip().lower()
        if key in seen:
            raise HTTPException(400, f"Username @{key} is used twice on this campus.")
        seen.add(key)

    row.name = name
    row.city = city
    if body.image_url is not None:
        row.image_url = body.image_url or ""
    row.updated_at = datetime.utcnow()

    existing = (
        db.query(User)
        .filter_by(role="branch_admin", branch_id=branch_id)
        .all()
    )
    by_username = {u.username.lower(): u for u in existing}
    keep: set[str] = set()

    try:
        for admin in body.admins:
            username = admin.username.strip().lower()
            current = by_username.get(username)
            if current:
                keep.add(current.id)
                current.full_name = (admin.name or current.full_name or username).strip()
                current.is_active = True
                if admin.email:
                    email = admin.email.strip().lower()
                    _assert_unique_login(db, email=email, username=username, exclude_user_id=current.id)
                    current.email = email
                password = (admin.password or "").strip()
                if password:
                    if len(password) < 6:
                        raise HTTPException(400, f"Password for @{username} must be at least 6 characters.")
                    current.password_hash = hash_password(password)
            else:
                created = _create_branch_admin(db, branch_id=branch_id, admin=admin, require_password=True)
                keep.add(created.id)

        for user in existing:
            if user.id not in keep:
                user.is_active = False

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Branch name conflict.")
    except Exception:
        db.rollback()
        raise

    db.refresh(row)
    return _public_branch(db, row)


@org_router.patch("/branches/{branch_id}/status")
def set_branch_status(
    branch_id: str,
    body: BranchStatusBody,
    _: User = Depends(require_roles("super_admin")),
    db: Session = Depends(get_db),
):
    """Soft activate / deactivate. Does not delete users, batches, or academic history."""
    row = _get_branch_or_404(db, branch_id)
    row.is_active = bool(body.is_active)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _public_branch(db, row)


@org_router.delete("/branches/{branch_id}")
def soft_delete_branch(
    branch_id: str,
    _: User = Depends(require_roles("super_admin")),
    db: Session = Depends(get_db),
):
    """
    Physical delete is refused. Soft-deactivates the campus so history stays intact.
    Super Admin can reactivate via PATCH .../status.
    """
    row = _get_branch_or_404(db, branch_id)
    row.is_active = False
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "policy": "soft_deactivate",
        "branch": _public_branch(db, row),
    }


def _public_batch(db: Session, b: Batch) -> dict:
    return {
        "id": b.id,
        "name": b.name,
        "schedule": b.schedule or "",
        "subjects": b.subjects or "",
        "branchId": b.branch_id or "",
        "branch_id": b.branch_id or "",
        "teacherId": b.teacher_id or "",
        "teacher_id": b.teacher_id or "",
        "students": count_students_in_batch(db, b),
        "avg": "—",
    }


@org_router.get("/batches")
def list_batches(
    branch_id: Optional[str] = None,
    user: User = Depends(require_roles("super_admin", "branch_admin", "teacher")),
    db: Session = Depends(get_db),
):
    """Campus batch catalog. Students use profile `batch` — no global directory."""
    q = db.query(Batch)
    scope = resolve_list_branch_scope(user, branch_id)
    if scope:
        q = q.filter(Batch.branch_id == scope)
    rows = q.order_by(Batch.name.asc()).all()
    payload: dict = {"batches": [_public_batch(db, b) for b in rows]}
    # Campus dashboards: unassigned only when list is branch-scoped (not SA global).
    if scope:
        payload["unassigned_students"] = count_unassigned_students(db, scope)
    return payload


@org_router.get("/batches/{batch_id}")
def get_batch(
    batch_id: str,
    actor: User = Depends(require_roles("super_admin", "branch_admin", "teacher")),
    db: Session = Depends(get_db),
):
    row = db.query(Batch).filter_by(id=batch_id).first()
    if not row:
        raise HTTPException(404, "Batch not found.")
    assert_can_access_batch(actor, row)
    return _public_batch(db, row)


@org_router.post("/batches")
def create_batch(
    body: BatchBody,
    actor: User = Depends(require_roles("super_admin", "branch_admin")),
    db: Session = Depends(get_db),
):
    branch_id = resolve_create_branch_id(actor, body.branch_id)
    branch_id = assert_branch_assignable(db, branch_id, require_active=True)
    name = assert_batch_name_available(db, branch_id, body.name)
    row = Batch(
        id=body.id or str(uuid.uuid4()),
        name=name,
        schedule=(body.schedule or "").strip(),
        subjects=(body.subjects or "").strip(),
        branch_id=branch_id,
        teacher_id="",
    )
    db.add(row)
    db.flush()
    if (body.teacher_id or "").strip():
        assign_batch_teacher(db, row, body.teacher_id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A batch with this name already exists on this campus.")
    db.refresh(row)
    return _public_batch(db, row)


@org_router.patch("/batches/{batch_id}")
def patch_batch(
    batch_id: str,
    body: BatchPatch,
    actor: User = Depends(require_roles("super_admin", "branch_admin")),
    db: Session = Depends(get_db),
):
    row = db.query(Batch).filter_by(id=batch_id).first()
    if not row:
        raise HTTPException(404, "Batch not found.")
    assert_can_access_batch(actor, row)
    # Campus must remain active for mutations.
    assert_branch_assignable(db, row.branch_id, require_active=True)

    if body.name is not None:
        rename_batch_cascade(db, row, body.name)
    if body.schedule is not None:
        row.schedule = body.schedule.strip()
    if body.subjects is not None:
        row.subjects = body.subjects.strip()
    if body.teacher_id is not None:
        assign_batch_teacher(db, row, body.teacher_id)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A batch with this name already exists on this campus.")
    db.refresh(row)
    return _public_batch(db, row)


@org_router.delete("/batches/{batch_id}")
def delete_batch(
    batch_id: str,
    actor: User = Depends(require_roles("super_admin", "branch_admin")),
    db: Session = Depends(get_db),
):
    """
    Physical delete only when no active students are assigned.
    Clears teacher pointer/CSV. Does not delete homework/notes/mocks/history
    (those keep historical batch_label strings).
    """
    row = db.query(Batch).filter_by(id=batch_id).first()
    if not row:
        raise HTTPException(404, "Batch not found.")
    assert_can_access_batch(actor, row)

    assigned = (
        db.query(User)
        .filter_by(role="student", branch_id=row.branch_id or "", batch=row.name)
        .count()
    )
    if assigned:
        raise HTTPException(
            409,
            "Unassign students from this batch before deleting it.",
        )

    assign_batch_teacher(db, row, "")
    db.delete(row)
    db.commit()
    return {"ok": True, "policy": "delete_unused"}
