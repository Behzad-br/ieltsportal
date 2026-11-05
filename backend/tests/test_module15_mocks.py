"""Module 15 — Mock exams assignment, ownership, linkage, scoring, IDOR."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.mocks.models import MockAssignment, MockAssignmentStudent, MockAttempt, MockIntegrityEvent
from app.mocks.scoring import compute_overall_band, ielts_round_half_up
from app.writing.models import WritingAttempt, WritingQuestion
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str, branch_id: str, *, batch: str | None = None, suffix: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M15 {branch_id}", city="M15", is_active=True))
        tag = suffix or uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"m15_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m15.test",
            username=uname,
            password_hash=hash_password("Passw0rd!"),
            full_name=uname,
            branch_id=branch_id,
            batch=batch,
            is_active=True,
        )
        db.add(row)
        if batch and not db.query(Batch).filter_by(branch_id=branch_id, name=batch).first():
            db.add(Batch(id=f"bat-{tag}", branch_id=branch_id, name=batch))
        db.commit()
        token = create_access_token(sub=row.id, role=role, extra={"branch_id": branch_id})
        return {"id": row.id, "token": token, "batch": batch, "branch_id": branch_id}
    finally:
        db.close()


def _writing_qs(db):
    q1 = WritingQuestion(
        public_id=f"M15-T1-{uuid.uuid4().hex[:6]}",
        test_type="academic",
        task_number=1,
        question_type="bar_chart",
        prompt="The chart shows buses.",
        minimum_words=150,
        recommended_minutes=20,
        status="published",
        source_type="original_generated",
    )
    q2 = WritingQuestion(
        public_id=f"M15-T2-{uuid.uuid4().hex[:6]}",
        test_type="academic",
        task_number=2,
        question_type="opinion",
        prompt="Some people prefer buses.",
        minimum_words=250,
        recommended_minutes=40,
        status="published",
        source_type="original_generated",
    )
    db.add_all([q1, q2])
    db.commit()
    db.refresh(q1)
    db.refresh(q2)
    return q1, q2


def test_overall_band_formula():
    assert ielts_round_half_up(6.75) == 7.0
    assert ielts_round_half_up(7.25) == 7.5
    assert compute_overall_band(7.0, 7.0, 6.5, 6.5) == 7.0
    assert compute_overall_band(7.5, 7.0, 6.5, 6.0) == 7.0
    assert compute_overall_band(8.0, 7.5, 7.0, 6.5) == 7.5
    assert compute_overall_band(6.0, None, None, None) == 6.0


def test_student_cannot_assign(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m15-c-{uuid.uuid4().hex[:6]}"
    stu = _mk("student", branch, batch="Morning")
    denied = client.post(
        "/api/mocks/assignments",
        headers=_hdr(stu["token"]),
        json={
            "title": "Nope",
            "mock_type": "writing",
            "paper_source": "manual",
            "paper_refs": {"prompt": "x"},
            "students": [{"id": stu["id"], "name": "Me"}],
        },
    )
    assert denied.status_code in (401, 403)


def test_assign_visibility_and_foreign_denied(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m15-a-{uuid.uuid4().hex[:6]}"
    b = f"m15-b-{uuid.uuid4().hex[:6]}"
    tea_a = _mk("teacher", a)
    tea_b = _mk("teacher", b)
    stu_a = _mk("student", a, batch="Morning")
    stu_b = _mk("student", b, batch="Morning")
    q1, q2 = _writing_qs(db)

    foreign = client.post(
        "/api/mocks/assignments",
        headers=_hdr(tea_a["token"]),
        json={
            "title": "Foreign",
            "mock_type": "writing",
            "paper_refs": {"task1_id": q1.id, "task2_id": q2.id},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu_b["id"], "name": "B"}],
        },
    )
    assert foreign.status_code == 403

    created = client.post(
        "/api/mocks/assignments",
        headers=_hdr(tea_a["token"]),
        json={
            "title": "Campus A Writing",
            "mock_type": "writing",
            "batch_label": "Morning",
            "paper_refs": {"task1_id": q1.id, "task2_id": q2.id},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu_a["id"], "name": "A"}],
        },
    )
    assert created.status_code == 200, created.text
    aid = created.json()["id"]
    db_row = db.query(MockAssignment).filter_by(id=aid).first()
    assert db_row is not None
    assert db_row.created_by == tea_a["id"]

    inbox_a = client.get("/api/mocks/student/inbox", headers=_hdr(stu_a["token"]))
    assert any(i["id"] == aid for i in inbox_a.json()["items"])
    inbox_b = client.get("/api/mocks/student/inbox", headers=_hdr(stu_b["token"]))
    assert all(i["id"] != aid for i in inbox_b.json()["items"])

    # Same-name batch spoof headers do not unlock
    spoof = client.get(
        f"/api/mocks/student/{aid}",
        headers={**_hdr(stu_b["token"]), "X-Student-Id": stu_a["id"], "X-Student-Batch": "Morning"},
    )
    assert spoof.status_code == 404

    # Teacher B cannot list/monitor Teacher A assignment
    lists = client.get("/api/mocks/assignments", headers=_hdr(tea_b["token"]))
    assert all(x["id"] != aid for x in lists.json()["assignments"])
    assert client.get(f"/api/mocks/assignments/{aid}/students", headers=_hdr(tea_b["token"])).status_code == 404


def test_start_resume_linkage_submit_review(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m15-s-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Alpha")
    other = _mk("student", branch, batch="Alpha", suffix=uuid.uuid4().hex[:8])
    q1, q2 = _writing_qs(db)

    created = client.post(
        "/api/mocks/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Writing lifecycle",
            "mock_type": "writing",
            "result_mode": "teacher",
            # Single question → writing-question piece (not full writing-mock).
            "paper_refs": {"question_id": q2.id},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu["id"], "name": "S"}],
        },
    )
    assert created.status_code == 200, created.text
    aid = created.json()["id"]

    start1 = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(stu["token"]),
        json={"assignment_id": aid, "consent": True},
    )
    assert start1.status_code == 200, start1.text
    attempt_id = start1.json()["attempt_id"]
    skill = start1.json()["skill"]
    assert skill.get("kind") == "writing-question"
    ref = skill.get("ref")

    # Resume returns same attempt
    start2 = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(stu["token"]),
        json={"assignment_id": aid, "consent": True},
    )
    assert start2.status_code == 200
    assert start2.json()["attempt_id"] == attempt_id

    wa = db.query(WritingAttempt).filter_by(id=ref).first()
    assert wa is not None
    assert wa.student_id == stu["id"]

    # Foreign student cannot access attempt
    assert (
        client.post(
            f"/api/mocks/attempts/{attempt_id}/submit",
            headers=_hdr(other["token"]),
            json={},
        ).status_code
        == 404
    )

    wa.estimated_band = 6.0
    db.commit()

    submit = client.post(f"/api/mocks/attempts/{attempt_id}/submit", headers=_hdr(stu["token"]), json={})
    assert submit.status_code == 200
    assert submit.json()["status"] == "submitted"
    # Idempotent
    again = client.post(f"/api/mocks/attempts/{attempt_id}/submit", headers=_hdr(stu["token"]), json={})
    assert again.status_code == 200
    assert again.json()["status"] == "submitted"
    events = db.query(MockIntegrityEvent).filter_by(attempt_id=attempt_id, event_type="TEST_SUBMITTED").count()
    assert events == 1

    attempt = db.query(MockAttempt).filter_by(id=attempt_id).first()
    assert attempt.writing_band == 6.0
    assert attempt.overall_band == 6.0
    assert attempt.published is False

    # Teacher review overrides and recomputes
    review = client.post(
        f"/api/mocks/attempts/{attempt_id}/review",
        headers=_hdr(teacher["token"]),
        json={"writing_band": 6.5, "publish": True},
    )
    assert review.status_code == 200
    assert review.json()["overall_band"] == 6.5
    assert review.json()["published"] is True
    db.refresh(attempt)
    assert attempt.writing_band == 6.5
    assert attempt.overall_band == 6.5


def test_speaking_mock_session_owned(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from app.speaking.models import TestSession

    branch = f"m15-sp-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Morning")
    created = client.post(
        "/api/mocks/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Speak mock",
            "mock_type": "speaking",
            "paper_source": "manual",
            "paper_refs": {"prompt": "Do you like music?"},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu["id"], "name": "S"}],
        },
    )
    assert created.status_code == 200, created.text
    start = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(stu["token"]),
        json={"assignment_id": created.json()["id"], "consent": True},
    )
    assert start.status_code == 200, start.text
    sid = start.json()["skill"].get("sessionId")
    db = _TestSessionFactory()
    try:
        sess = db.query(TestSession).filter_by(id=sid).first()
        assert sess is not None
        assert sess.student_id == stu["id"]
        att = db.query(MockAttempt).filter_by(id=start.json()["attempt_id"]).first()
        assert att.student_id == stu["id"]
    finally:
        db.close()


def test_integrity_events_teacher_scoped(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m15-e-{uuid.uuid4().hex[:6]}"
    b = f"m15-f-{uuid.uuid4().hex[:6]}"
    tea_a = _mk("teacher", a)
    tea_b = _mk("teacher", b)
    stu = _mk("student", a, batch="Morning")
    q1, q2 = _writing_qs(db)
    created = client.post(
        "/api/mocks/assignments",
        headers=_hdr(tea_a["token"]),
        json={
            "title": "Events",
            "mock_type": "writing",
            "paper_refs": {"task1_id": q1.id, "task2_id": q2.id},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu["id"], "name": "S"}],
        },
    )
    start = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(stu["token"]),
        json={"assignment_id": created.json()["id"], "consent": True},
    )
    attempt_id = start.json()["attempt_id"]
    client.post(
        f"/api/mocks/attempts/{attempt_id}/heartbeat",
        headers=_hdr(stu["token"]),
        json={"event_type": "TAB_HIDDEN"},
    )
    ok = client.get(f"/api/mocks/attempts/{attempt_id}/events", headers=_hdr(tea_a["token"]))
    assert ok.status_code == 200
    assert any(e["event_type"] == "TAB_HIDDEN" for e in ok.json()["events"])
    denied = client.get(f"/api/mocks/attempts/{attempt_id}/events", headers=_hdr(tea_b["token"]))
    assert denied.status_code == 404
