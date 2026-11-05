"""Module 19 — Full database integrity: schema, uniqueness, refs, transactions."""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.database import Base
from app.db_integrity import EXPECTED_TABLES, MIGRATION_HEAD, skill_ref_cross_user
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.mocks.models import MockAssignment, MockAssignmentStudent, MockAttempt
from app.reading.models import ReadingAttempt
from app.speaking.models import TestSession
from app.writing.models import WritingAttempt, WritingQuestion
from tests.conftest import _TestSessionFactory
from tests.test_migrations_phase3 import HEAD as MIGRATION_TEST_HEAD


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str, branch_id: str, *, batch: str | None = None, suffix: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M19 {branch_id}", city="M19", is_active=True))
        tag = suffix or uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"m19_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m19.test",
            username=uname,
            password_hash=hash_password("Passw0rd!"),
            full_name=uname,
            branch_id=branch_id,
            batch=batch or "",
            is_active=True,
        )
        db.add(row)
        if batch and not db.query(Batch).filter_by(branch_id=branch_id, name=batch).first():
            db.add(Batch(id=f"bat-{tag}", branch_id=branch_id, name=batch))
        db.commit()
        token = create_access_token(sub=row.id, role=role, extra={"branch_id": branch_id})
        return {"id": row.id, "token": token, "username": uname, "branch_id": branch_id, "batch": batch}
    finally:
        db.close()


# ── Schema / migration head ──────────────────────────────────────────────────


def test_expected_orm_tables_registered():
    names = set(Base.metadata.tables.keys())
    missing = EXPECTED_TABLES - names
    assert not missing, f"ORM missing tables: {missing}"


def test_migration_head_constant_aligned():
    assert MIGRATION_HEAD == MIGRATION_TEST_HEAD == "20261012_0007"


def test_unique_constraints_present_on_metadata():
    batches = Base.metadata.tables["auth_batches"]
    hw = Base.metadata.tables["homework_submissions"]
    slots = Base.metadata.tables["mock_assignment_students"]
    batch_uq = {tuple(c.name for c in u.columns) for u in batches.constraints if hasattr(u, "columns") and u.__class__.__name__ == "UniqueConstraint"}
    hw_uq = {tuple(c.name for c in u.columns) for u in hw.constraints if u.__class__.__name__ == "UniqueConstraint"}
    slot_uq = {tuple(c.name for c in u.columns) for u in slots.constraints if u.__class__.__name__ == "UniqueConstraint"}
    assert ("branch_id", "name") in batch_uq
    assert ("assignment_id", "student_id") in hw_uq
    assert ("assignment_id", "student_id") in slot_uq


# ── Uniqueness enforcement ───────────────────────────────────────────────────


def test_duplicate_batch_rejected(db):
    bid = f"m19-b-{uuid.uuid4().hex[:6]}"
    db.add(Branch(id=bid, name="Campus", is_active=True))
    db.add(Batch(id=str(uuid.uuid4()), branch_id=bid, name="Morning"))
    db.commit()
    db.add(Batch(id=str(uuid.uuid4()), branch_id=bid, name="Morning"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_duplicate_homework_submission_rejected(db):
    aid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    db.add(
        HomeworkAssignment(
            id=aid,
            title="M19",
            module="writing",
            created_by="t1",
        )
    )
    db.add(HomeworkSubmission(assignment_id=aid, student_id=sid, status="pending"))
    db.commit()
    db.add(HomeworkSubmission(assignment_id=aid, student_id=sid, status="pending"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_duplicate_mock_slot_rejected(db):
    asg = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    db.add(
        MockAssignment(
            id=asg,
            title="M19 Mock",
            mock_type="writing",
            duration_minutes=60,
        )
    )
    db.add(MockAssignmentStudent(assignment_id=asg, student_id=sid))
    db.commit()
    db.add(MockAssignmentStudent(assignment_id=asg, student_id=sid))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_duplicate_email_rejected(db):
    db.add(
        User(
            id=str(uuid.uuid4()),
            role="student",
            email="dup@m19.test",
            username=f"u1_{uuid.uuid4().hex[:6]}",
            password_hash="x",
            branch_id="",
        )
    )
    db.commit()
    db.add(
        User(
            id=str(uuid.uuid4()),
            role="student",
            email="dup@m19.test",
            username=f"u2_{uuid.uuid4().hex[:6]}",
            password_hash="x",
            branch_id="",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ── Logical reference integrity ──────────────────────────────────────────────


def test_skill_ref_cross_user_detected(db):
    owner = str(uuid.uuid4())
    other = str(uuid.uuid4())
    q = WritingQuestion(
        id=str(uuid.uuid4()),
        public_id=f"m19-{uuid.uuid4().hex[:8]}",
        test_type="academic",
        task_number=2,
        question_type="essay",
        prompt="Discuss.",
        status="published",
    )
    db.add(q)
    attempt = WritingAttempt(
        id=str(uuid.uuid4()),
        student_id=owner,
        question_id=q.id,
        status="in_progress",
    )
    db.add(attempt)
    db.commit()
    skill = {"kind": "writing-question", "ref": attempt.id}
    assert skill_ref_cross_user(db, other, skill) is True
    assert skill_ref_cross_user(db, owner, skill) is False


def test_skill_ref_missing_row_not_cross_user(db):
    skill = {"kind": "speaking", "sessionId": str(uuid.uuid4()), "ref": str(uuid.uuid4())}
    assert skill_ref_cross_user(db, "any-student", skill) is False


def test_diagnostic_detects_seeded_orphan_attempt(db):
    orphan_sid = f"ghost-{uuid.uuid4().hex[:8]}"
    row = ReadingAttempt(
        student_id=orphan_sid,
        test_id="test-001",
        test_type="academic",
        status="in_progress",
    )
    db.add(row)
    db.commit()
    users = {u.id for u in db.query(User.id).all()}
    orphans = db.query(ReadingAttempt).filter(ReadingAttempt.student_id == orphan_sid).all()
    assert orphans
    assert orphans[0].student_id not in users


# ── App-level rejection paths ────────────────────────────────────────────────


def test_invalid_batch_on_homework_create(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m19-c-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch, batch="Morning")
    stu = _mk("student", branch, batch="Morning")
    res = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Bad batch",
            "module": "writing",
            "payload": {"prompt": "x"},
            "batch_label": "DoesNotExist",
            "assignment_target": "batch",
            "student_ids": [stu["id"]],
        },
    )
    assert res.status_code in (400, 403), res.text


def test_homework_already_submitted_returns_409(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m19-h-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch, batch="Morning")
    stu = _mk("student", branch, batch="Morning")
    create = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "M19 HW",
            "module": "writing",
            "source": "manual",
            "payload": {"prompt": "Write."},
            "student_ids": [stu["id"]],
            "assignment_target": "specific",
        },
    )
    assert create.status_code == 200, create.text
    aid = create.json()["id"]
    db = _TestSessionFactory()
    try:
        db.add(
            HomeworkSubmission(
                assignment_id=aid,
                student_id=stu["id"],
                status="submitted",
                submitted_at=datetime.utcnow(),
            )
        )
        db.commit()
    finally:
        db.close()
    start = client.post(
        f"/api/homework/student/{aid}/start",
        headers=_hdr(stu["token"]),
    )
    assert start.status_code == 409


# ── Transaction rollback ─────────────────────────────────────────────────────


def test_multi_row_failure_rolls_back(db):
    bid = f"m19-tx-{uuid.uuid4().hex[:6]}"
    db.add(Branch(id=bid, name="TX Campus", is_active=True))
    db.add(Batch(id=str(uuid.uuid4()), branch_id=bid, name="Alpha"))
    db.flush()
    db.add(Batch(id=str(uuid.uuid4()), branch_id=bid, name="Alpha"))  # unique violation
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(Batch).filter_by(branch_id=bid).count() == 0
    assert db.query(Branch).filter_by(id=bid).count() == 0


def test_get_db_rollback_on_exception(client, monkeypatch):
    """Unhandled path: session rollback leaves no half-written homework row."""
    from app.config import settings
    from app import homework as hw_pkg

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m19-rb-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch, batch="Morning")
    stu = _mk("student", branch, batch="Morning")
    create = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "RB HW",
            "module": "writing",
            "source": "manual",
            "payload": {"prompt": "Write."},
            "student_ids": [stu["id"]],
            "assignment_target": "specific",
        },
    )
    assert create.status_code == 200, create.text
    aid = create.json()["id"]

    async def boom(*_a, **_k):
        raise RuntimeError("simulated skill open failure")

    monkeypatch.setattr(hw_pkg.controllers, "_open_skill", boom)
    with pytest.raises(RuntimeError):
        client.post(f"/api/homework/student/{aid}/start", headers=_hdr(stu["token"]))

    db = _TestSessionFactory()
    try:
        # Flush without commit should not persist after request error + rollback.
        n = db.query(HomeworkSubmission).filter_by(assignment_id=aid, student_id=stu["id"]).count()
        assert n == 0
    finally:
        db.close()
