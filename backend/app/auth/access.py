"""Reusable authorization helpers for branch isolation and ownership (Module 3)."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.models import Batch, Branch, User


def assert_user_campus_active(db: Session, user: User) -> None:
    """
    Non–super-admin users assigned to a deactivated (or missing) campus cannot authenticate
    or continue a session. Historic academic rows are preserved; access is blocked centrally.
    """
    if user.role == "super_admin":
        return
    bid = (user.branch_id or "").strip()
    if not bid:
        if user.role in {"branch_admin", "teacher", "student"}:
            raise HTTPException(403, "Account is missing a branch assignment.")
        return
    branch = db.query(Branch).filter_by(id=bid).first()
    if branch is None:
        raise HTTPException(403, "Campus assignment is invalid.")
    if not bool(getattr(branch, "is_active", True)):
        raise HTTPException(403, "This campus is deactivated. Contact Super Admin.")


def assert_same_branch(actor: User, target_branch_id: str | None, *, detail: str = "Wrong branch.") -> None:
    """Super admin bypasses; everyone else must share a non-empty branch_id."""
    if actor.role == "super_admin":
        return
    left = (actor.branch_id or "").strip()
    right = (target_branch_id or "").strip()
    if not left or not right or left != right:
        raise HTTPException(403, detail)


def assert_branch_assignable(
    db: Session,
    branch_id: str | None,
    *,
    require_active: bool = True,
    allow_empty: bool = False,
) -> str:
    """
    Validate branch_id for create/reassignment.
    Empty only when allow_empty (e.g. super_admin). Soft-deactivated campuses reject new assignments.
    """
    bid = (branch_id or "").strip()
    if not bid:
        if allow_empty:
            return ""
        raise HTTPException(400, "branch_id is required.")
    branch = db.query(Branch).filter_by(id=bid).first()
    if branch is None:
        raise HTTPException(404, "Branch not found.")
    if require_active and not bool(getattr(branch, "is_active", True)):
        raise HTTPException(400, "Cannot assign users to a deactivated campus.")
    return bid


def assert_can_manage_user(actor: User, target: User) -> None:
    """
    Org user PATCH/DELETE authorization.

    - Super admin: any user
    - Self: profile updates (password/name/username/email) — not peer admin control
    - Branch admin: teachers and students on same campus only (not other branch_admins)
    - Teacher: students on same campus only
    """
    if actor.role == "super_admin":
        return
    if actor.id == target.id:
        return
    if actor.role == "branch_admin":
        if target.role not in {"teacher", "student"}:
            raise HTTPException(403, "Branch admins may only manage teachers and students.")
        assert_same_branch(actor, target.branch_id)
        return
    if actor.role == "teacher":
        if target.role != "student":
            raise HTTPException(403, "Not allowed.")
        assert_same_branch(actor, target.branch_id)
        return
    raise HTTPException(403, "Not allowed.")


def assert_can_view_user(actor: User, target: User) -> None:
    """Org user GET detail authorization."""
    if actor.role == "super_admin":
        return
    if actor.id == target.id:
        return
    if actor.role == "branch_admin":
        if target.role not in {"teacher", "student"}:
            raise HTTPException(403, "Not allowed.")
        assert_same_branch(actor, target.branch_id)
        return
    if actor.role == "teacher":
        if target.role != "student":
            raise HTTPException(403, "Not allowed.")
        assert_same_branch(actor, target.branch_id)
        return
    raise HTTPException(403, "Not allowed.")


def resolve_create_branch_id(actor: User, requested: str | None) -> str:
    """
    Branch for new users/batches.
    Non–super-admin: always actor.branch_id (body branch_id cannot escalate).
    """
    requested = (requested or "").strip()
    if actor.role == "super_admin":
        return requested
    if not (actor.branch_id or "").strip():
        raise HTTPException(403, "Account is missing a branch assignment.")
    if requested and requested != actor.branch_id:
        raise HTTPException(403, "Wrong branch.")
    return actor.branch_id


def resolve_list_branch_scope(actor: User, requested: str | None) -> str | None:
    """
    Branch filter for list endpoints.
    Returns None only for super_admin global lists.
    Non–super-admin with no branch → empty scope sentinel via 403.
    """
    requested = (requested or "").strip() or None
    if actor.role == "super_admin":
        return requested
    if not (actor.branch_id or "").strip():
        raise HTTPException(403, "Account is missing a branch assignment.")
    if requested and requested != actor.branch_id:
        raise HTTPException(403, "Wrong branch.")
    return actor.branch_id


def student_batch_from_db(db: Session, student_id: str) -> str:
    """Canonical batch label for unlock/visibility — never trust client headers."""
    row = db.query(User).filter_by(id=student_id, role="student").first()
    return (row.batch or "").strip() if row else ""


def normalize_batch_name(raw: str | None) -> str:
    """Trim only — retain display casing; uniqueness is case-insensitive per campus."""
    return (raw or "").strip()


def find_batch_on_branch(db: Session, branch_id: str, batch_label: str) -> Batch | None:
    """Match catalog row by campus + case-insensitive name."""
    bid = (branch_id or "").strip()
    label = normalize_batch_name(batch_label)
    if not bid or not label:
        return None
    needle = label.lower()
    for row in db.query(Batch).filter_by(branch_id=bid).all():
        if (row.name or "").strip().lower() == needle:
            return row
    return None


def assert_batch_name_available(
    db: Session,
    branch_id: str,
    name: str,
    *,
    exclude_batch_id: str | None = None,
) -> str:
    """Enforce non-empty + unique (branch_id, casefold(name)). Returns normalized name."""
    label = normalize_batch_name(name)
    if not label:
        raise HTTPException(400, "Batch name is required.")
    bid = (branch_id or "").strip()
    if not bid:
        raise HTTPException(400, "branch_id is required.")
    existing = find_batch_on_branch(db, bid, label)
    if existing and existing.id != (exclude_batch_id or ""):
        raise HTTPException(409, "A batch with this name already exists on this campus.")
    return label


def assert_batch_on_branch(
    db: Session,
    branch_id: str | None,
    batch_label: str | None,
    *,
    allow_empty: bool = True,
) -> str:
    """
    Student primary batch is auth_users.batch (batch name label on the same campus).
    Empty is allowed (unassigned). Non-empty must match an auth_batches.name on branch_id.
    Returns the canonical catalog name (preserves catalog casing).
    """
    label = normalize_batch_name(batch_label)
    if not label:
        if allow_empty:
            return ""
        raise HTTPException(400, "batch is required.")
    bid = (branch_id or "").strip()
    if not bid:
        raise HTTPException(400, "branch_id is required to assign a batch.")
    exists = find_batch_on_branch(db, bid, label)
    if exists is None:
        raise HTTPException(400, "Batch is not on this campus.")
    return exists.name


def users_in_branch(db: Session, branch_id: str, *, role: str | None = None) -> list[str]:
    q = db.query(User.id).filter(User.branch_id == branch_id, User.is_active == True)  # noqa: E712
    if role:
        q = q.filter(User.role == role)
    return [r[0] for r in q.all()]


def teacher_batch_names(teacher: User) -> set[str]:
    """Assigned batch labels from auth_users.batches CSV (Module 6 linkage — no junction table)."""
    return {
        part.strip()
        for part in (teacher.batches or "").split(",")
        if part.strip() and part.strip() != "—"
    }


def _set_teacher_batch_csv(teacher: User, names: set[str]) -> None:
    teacher.batches = ", ".join(sorted(n for n in names if n))


def sync_teacher_batch_links(db: Session, teacher: User) -> None:
    """
    CSV → Batch.teacher_id (teacher profile path).
    Authoritative for multi-batch teacher claims via User.batches.
    """
    if teacher.role != "teacher":
        return
    names = teacher_batch_names(teacher)
    names_l = {n.lower() for n in names}
    for batch in db.query(Batch).filter(Batch.teacher_id == teacher.id).all():
        if (batch.name or "").lower() not in names_l:
            batch.teacher_id = ""
    bid = (teacher.branch_id or "").strip()
    if not bid or not names:
        return
    # Canonicalize CSV to catalog names on this campus.
    canonical: set[str] = set()
    for label in names:
        row = find_batch_on_branch(db, bid, label)
        if row:
            canonical.add(row.name)
            row.teacher_id = teacher.id
    if canonical != names:
        _set_teacher_batch_csv(teacher, canonical)


def clear_teacher_batch_links(db: Session, teacher: User) -> None:
    """On branch reassignment / deactivate prep: drop Batch.teacher_id pointers; clear CSV."""
    if teacher.role != "teacher":
        return
    for batch in db.query(Batch).filter(Batch.teacher_id == teacher.id).all():
        batch.teacher_id = ""
    teacher.batches = ""


def assign_batch_teacher(db: Session, batch: Batch, teacher_id: str | None) -> None:
    """
    Batch.teacher_id → teacher CSV (batch edit path).
    Authoritative pointer for who teaches this catalog batch.
    Empty teacher_id clears the assignment.
    """
    new_tid = (teacher_id or "").strip()
    old_tid = (batch.teacher_id or "").strip()
    name = (batch.name or "").strip()
    if not name:
        raise HTTPException(400, "Batch name is required before assigning a teacher.")

    if old_tid and old_tid != new_tid:
        prev = db.query(User).filter_by(id=old_tid, role="teacher").first()
        if prev:
            names = teacher_batch_names(prev)
            names = {n for n in names if n.lower() != name.lower()}
            _set_teacher_batch_csv(prev, names)

    if not new_tid:
        batch.teacher_id = ""
        return

    teacher = db.query(User).filter_by(id=new_tid, role="teacher").first()
    if teacher is None:
        raise HTTPException(400, "Teacher not found.")
    if not teacher.is_active:
        raise HTTPException(400, "Teacher is inactive.")
    if (teacher.branch_id or "").strip() != (batch.branch_id or "").strip():
        raise HTTPException(400, "Teacher is not on this campus.")

    batch.teacher_id = teacher.id
    names = teacher_batch_names(teacher)
    names = {n for n in names if n.lower() != name.lower()}
    names.add(name)
    _set_teacher_batch_csv(teacher, names)


def rename_batch_cascade(db: Session, batch: Batch, new_name: str) -> str:
    """
    Rename catalog name and cascade living membership / resource labels on this campus.
    Historical attempt/result rows are not rewritten.
    Homework/notes/mocks that still use the old label on this campus are updated so
    visibility keeps working (name-based product contract).
    """
    old = (batch.name or "").strip()
    new = assert_batch_name_available(db, batch.branch_id or "", new_name, exclude_batch_id=batch.id)
    if old == new:
        return new

    bid = (batch.branch_id or "").strip()
    batch.name = new

    for student in db.query(User).filter_by(role="student", branch_id=bid, batch=old).all():
        student.batch = new

    for teacher in db.query(User).filter_by(role="teacher", branch_id=bid).all():
        names = teacher_batch_names(teacher)
        if any(n.lower() == old.lower() for n in names):
            names = {n for n in names if n.lower() != old.lower()}
            names.add(new)
            _set_teacher_batch_csv(teacher, names)

    staff_ids = {
        u.id
        for u in db.query(User)
        .filter(User.branch_id == bid, User.role.in_(("teacher", "branch_admin", "super_admin")))
        .all()
    }

    from app.homework.models import HomeworkAssignment

    for hw in db.query(HomeworkAssignment).filter_by(batch_label=old).all():
        if (hw.created_by or "") in staff_ids:
            hw.batch_label = new

    from app.mocks.models import MockAssignment

    for mock in db.query(MockAssignment).filter_by(batch_id=batch.id).all():
        mock.batch_label = new
    for mock in db.query(MockAssignment).filter_by(batch_label=old).all():
        if (mock.created_by or "") in staff_ids:
            mock.batch_label = new

    from app.notes.models import ClassNote

    for note in db.query(ClassNote).all():
        if (note.created_by or "") not in staff_ids:
            continue
        batches = list(note.unlocked_batches or [])
        if any((b or "").strip() == old for b in batches):
            note.unlocked_batches = [new if (b or "").strip() == old else b for b in batches]

    return new


def assert_can_access_batch(actor: User, batch: Batch) -> None:
    """Branch-scoped batch read/manage gate."""
    if actor.role == "super_admin":
        return
    assert_same_branch(actor, batch.branch_id)


def count_students_in_batch(db: Session, batch: Batch) -> int:
    """Students whose primary label matches this campus batch (branch-scoped)."""
    return (
        db.query(User)
        .filter_by(
            role="student",
            branch_id=batch.branch_id or "",
            batch=batch.name,
            is_active=True,
        )
        .count()
    )


def count_unassigned_students(db: Session, branch_id: str) -> int:
    """
    Active students on campus with empty batch OR a label not in the campus catalog.
    Branch-scoped only — never global.
    """
    bid = (branch_id or "").strip()
    if not bid:
        return 0
    catalog = {(b.name or "").strip() for b in db.query(Batch).filter_by(branch_id=bid).all() if (b.name or "").strip()}
    students = (
        db.query(User)
        .filter_by(role="student", branch_id=bid, is_active=True)
        .all()
    )
    return sum(1 for s in students if (s.batch or "").strip() not in catalog)


def assert_student_in_actor_branch(db: Session, actor: User, student_id: str) -> User:
    """Teacher/BA may only touch students on their own active campus."""
    student = db.query(User).filter_by(id=student_id, role="student").first()
    if not student or not student.is_active:
        raise HTTPException(404, "Student not found.")
    if actor.role == "super_admin":
        return student
    assert_same_branch(actor, student.branch_id, detail="Wrong branch.")
    return student


def repair_teacher_csv_from_batch_pointers(db: Session) -> int:
    """
    Idempotent legacy repair: when Batch.teacher_id is set but teacher.batches CSV
    omits that catalog name, add the canonical name. Does not invent students or
    rename labels. Returns number of teacher rows updated.
    """
    touched: set[str] = set()
    for batch in db.query(Batch).all():
        tid = (batch.teacher_id or "").strip()
        name = (batch.name or "").strip()
        if not tid or not name:
            continue
        teacher = db.query(User).filter_by(id=tid, role="teacher").first()
        if teacher is None:
            continue
        if (teacher.branch_id or "").strip() != (batch.branch_id or "").strip():
            continue
        names = teacher_batch_names(teacher)
        if any(n.lower() == name.lower() for n in names):
            continue
        names.add(name)
        _set_teacher_batch_csv(teacher, names)
        touched.add(teacher.id)
    return len(touched)
