"""Module 21 — API consistency: routes, auth statuses, errors, idempotency, OpenAPI."""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime

from fastapi.openapi.utils import get_openapi
from sqlalchemy.exc import IntegrityError

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.main import app
from app.mocks.models import MockAnswer, MockAssignment, MockAssignmentStudent, MockAttempt
from app.reading.models import ReadingAttempt
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str, branch_id: str, *, batch: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M21 {branch_id}", city="M21", is_active=True))
        tag = uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"m21_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m21.test",
            username=uname,
            password_hash=hash_password("Passw0rd!"),
            full_name=uname,
            branch_id="" if role == "super_admin" else branch_id,
            batch=batch or "",
            is_active=True,
        )
        db.add(row)
        if batch and not db.query(Batch).filter_by(branch_id=branch_id, name=batch).first():
            db.add(Batch(id=f"bat-{tag}", branch_id=branch_id, name=batch))
        db.commit()
        token = create_access_token(
            sub=row.id,
            role=role,
            extra={"branch_id": row.branch_id or ""},
        )
        return {"id": row.id, "token": token, "branch_id": branch_id, "batch": batch}
    finally:
        db.close()


# ── Routes / OpenAPI ─────────────────────────────────────────────────────────


def test_no_duplicate_method_path_registrations():
    pairs = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if not path or not methods:
            continue
        for m in methods:
            if m in {"HEAD", "OPTIONS"}:
                continue
            pairs.append((m, path))
    counts = Counter(pairs)
    dups = [f"{m} {p}" for (m, p), n in counts.items() if n > 1]
    assert not dups, f"Duplicate routes: {dups}"


def test_expected_route_groups_exist():
    paths = {getattr(r, "path", "") for r in app.routes}
    for required in (
        "/api/auth/login",
        "/api/auth/me",
        "/api/org/batches",
        "/api/reading/attempts",
        "/api/listening/attempts",
        "/api/writing/questions",
        "/api/tests/start",
        "/api/homework/student/{assignment_id}/start",
        "/api/notes/student",
        "/api/mocks/attempts/{attempt_id}",
        "/api/analytics/student",
        "/api/health",
    ):
        assert required in paths, f"missing route {required}"


def test_openapi_generates_without_duplicate_operation_ids():
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    ops = []
    for path_item in schema.get("paths", {}).values():
        for method, op in path_item.items():
            if method.startswith("x-") or not isinstance(op, dict):
                continue
            oid = op.get("operationId")
            if oid:
                ops.append(oid)
    counts = Counter(ops)
    dups = [k for k, n in counts.items() if n > 1]
    assert not dups, f"duplicate operationIds: {dups}"
    assert len(ops) > 50


# ── Auth / status ────────────────────────────────────────────────────────────


def test_unauthenticated_me_returns_401(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    res = client.get("/api/auth/me")
    assert res.status_code == 401
    assert "detail" in res.json()


def test_student_forbidden_on_org_users(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m21-f-{uuid.uuid4().hex[:6]}"
    stu = _mk("student", branch, batch="Morning")
    res = client.get("/api/org/users", headers=_hdr(stu["token"]))
    assert res.status_code == 403
    assert isinstance(res.json().get("detail"), str)


def test_foreign_reading_attempt_hidden_404(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m21-a-{uuid.uuid4().hex[:6]}"
    b = f"m21-b-{uuid.uuid4().hex[:6]}"
    owner = _mk("student", a, batch="Morning")
    other = _mk("student", b, batch="Morning")
    db = _TestSessionFactory()
    try:
        row = ReadingAttempt(
            student_id=owner["id"],
            test_id="test-001",
            test_type="academic",
            status="in_progress",
        )
        db.add(row)
        db.commit()
        aid = row.id
    finally:
        db.close()
    res = client.get(f"/api/reading/attempts/{aid}", headers=_hdr(other["token"]))
    assert res.status_code in (403, 404)
    body = res.json()
    assert "detail" in body
    assert "password" not in res.text.lower()


def test_batch_conflict_returns_409(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m21-c-{uuid.uuid4().hex[:6]}"
    ba = _mk("branch_admin", branch)
    first = client.post(
        "/api/org/batches",
        headers=_hdr(ba["token"]),
        json={"name": "Morning", "branch_id": branch},
    )
    assert first.status_code in (200, 201), first.text
    second = client.post(
        "/api/org/batches",
        headers=_hdr(ba["token"]),
        json={"name": "Morning", "branch_id": branch},
    )
    assert second.status_code == 409
    assert isinstance(second.json().get("detail"), str)


# ── Null bands / timestamps / idempotency ────────────────────────────────────


def test_pending_bands_are_null_not_zero(db):
    row = ReadingAttempt(
        student_id=str(uuid.uuid4()),
        test_id="t1",
        test_type="academic",
        status="in_progress",
        estimated_band=None,
    )
    db.add(row)
    db.commit()
    assert row.estimated_band is None
    assert row.estimated_band != 0


def test_iso_timestamp_helper_on_mock_work(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m21-m-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch, batch="Morning")
    stu = _mk("student", branch, batch="Morning")
    db = _TestSessionFactory()
    try:
        asg = MockAssignment(
            id=str(uuid.uuid4()),
            title="M21 Mock",
            mock_type="writing",
            duration_minutes=30,
            created_by=teacher["id"],
            batch_label="Morning",
        )
        db.add(asg)
        slot = MockAssignmentStudent(
            id=str(uuid.uuid4()),
            assignment_id=asg.id,
            student_id=stu["id"],
            student_name="Stu",
            status="in_progress",
        )
        db.add(slot)
        attempt = MockAttempt(
            id=str(uuid.uuid4()),
            assignment_student_id=slot.id,
            assignment_id=asg.id,
            student_id=stu["id"],
            status="submitted",
            submitted_at=datetime.utcnow(),
            published=False,
        )
        db.add(attempt)
        db.add(
            MockAnswer(
                attempt_id=attempt.id,
                question_key="q1",
                value_json='"hello"',
            )
        )
        db.commit()
        aid = attempt.id
    finally:
        db.close()

    res = client.get(f"/api/mocks/attempts/{aid}", headers=_hdr(teacher["token"]))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["attempt_id"] == aid
    assert data["student_id"] == stu["id"]
    assert data["published"] is False
    assert data["result_pending"] is True
    assert data["overall_band"] is None or isinstance(data["overall_band"], (int, float))
    assert data["submitted_at"] is None or "T" in str(data["submitted_at"])
    assert isinstance(data["answers"], list)


def test_foreign_teacher_cannot_see_mock_attempt(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m21-ta-{uuid.uuid4().hex[:6]}"
    b = f"m21-tb-{uuid.uuid4().hex[:6]}"
    teacher_a = _mk("teacher", a, batch="Morning")
    teacher_b = _mk("teacher", b, batch="Morning")
    stu = _mk("student", a, batch="Morning")
    db = _TestSessionFactory()
    try:
        asg = MockAssignment(
            id=str(uuid.uuid4()),
            title="Private",
            mock_type="reading",
            duration_minutes=30,
            created_by=teacher_a["id"],
        )
        db.add(asg)
        slot = MockAssignmentStudent(
            assignment_id=asg.id, student_id=stu["id"], status="assigned"
        )
        db.add(slot)
        db.flush()
        attempt = MockAttempt(
            assignment_student_id=slot.id,
            assignment_id=asg.id,
            student_id=stu["id"],
            status="in_progress",
        )
        db.add(attempt)
        db.commit()
        aid = attempt.id
    finally:
        db.close()
    res = client.get(f"/api/mocks/attempts/{aid}", headers=_hdr(teacher_b["token"]))
    assert res.status_code == 404


def test_homework_complete_idempotent_shape(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m21-h-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch, batch="Morning")
    stu = _mk("student", branch, batch="Morning")
    create = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "M21 HW",
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
        sub = HomeworkSubmission(
            assignment_id=aid,
            student_id=stu["id"],
            status="submitted",
            submitted_at=datetime.utcnow(),
            estimated_band=6.5,
        )
        db.add(sub)
        db.commit()
        sid = sub.id
    finally:
        db.close()
    first = client.post(
        f"/api/homework/student/submissions/{sid}/complete",
        headers=_hdr(stu["token"]),
        json={},
    )
    second = client.post(
        f"/api/homework/student/submissions/{sid}/complete",
        headers=_hdr(stu["token"]),
        json={},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert set(first.json().keys()) == set(second.json().keys())
    assert first.json()["status"] == second.json()["status"] == "submitted"


def test_validation_error_has_detail(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    res = client.post("/api/auth/login", json={})
    assert res.status_code == 422
    detail = res.json().get("detail")
    assert detail is not None
