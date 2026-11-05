"""Module 13 — Homework create/visibility/submit/review/IDOR + Speaking ownership."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.speaking.models import TestSession
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str, branch_id: str, *, batch: str | None = None, suffix: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M13 {branch_id}", city="M13", is_active=True))
        tag = suffix or uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"m13_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m13.test",
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
        return {"id": row.id, "token": token, "username": uname, "branch_id": branch_id, "batch": batch}
    finally:
        db.close()


def _create_hw(client, teacher: dict, **body) -> dict:
    payload = {
        "title": body.get("title") or "M13 HW",
        "module": body.get("module") or "writing",
        "scope": "piece",
        "source": "manual",
        "payload": body.get("payload") or {"prompt": "Write about education."},
        "student_ids": body.get("student_ids") or [],
        "batch_label": body.get("batch_label"),
        "deadline": body.get("deadline"),
        "assignment_target": body.get("assignment_target") or "specific",
    }
    res = client.post("/api/homework/teacher/assignments", headers=_hdr(teacher["token"]), json=payload)
    assert res.status_code == 200, res.text
    return res.json()


def test_student_cannot_create_homework(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m13-c-{uuid.uuid4().hex[:6]}"
    stu = _mk("student", branch)
    denied = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(stu["token"]),
        json={"title": "Nope", "module": "writing", "payload": {"prompt": "x"}},
    )
    assert denied.status_code == 403


def test_create_direct_and_foreign_target_denied(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m13-a-{uuid.uuid4().hex[:6]}"
    b = f"m13-b-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", a)
    stu_a = _mk("student", a, suffix=uuid.uuid4().hex[:8])
    stu_b = _mk("student", b, suffix=uuid.uuid4().hex[:8])

    ok = _create_hw(client, teacher, student_ids=[stu_a["id"]])
    assert ok["id"]
    db = _TestSessionFactory()
    try:
        row = db.query(HomeworkAssignment).filter_by(id=ok["id"]).first()
        assert row is not None
        assert row.created_by == teacher["id"]
        assert stu_a["id"] in row.student_ids
    finally:
        db.close()

    foreign = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Foreign",
            "module": "writing",
            "payload": {"prompt": "x"},
            "student_ids": [stu_b["id"]],
        },
    )
    assert foreign.status_code == 403


def test_visibility_direct_batch_and_spoof(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m13-v-{uuid.uuid4().hex[:6]}"
    b = f"m13-w-{uuid.uuid4().hex[:6]}"
    teacher_a = _mk("teacher", a)
    teacher_b = _mk("teacher", b, suffix=uuid.uuid4().hex[:8])
    stu_a = _mk("student", a, batch="Morning A", suffix=uuid.uuid4().hex[:8])
    stu_a2 = _mk("student", a, batch="Morning A", suffix=uuid.uuid4().hex[:8])
    stu_b = _mk("student", b, batch="Morning A", suffix=uuid.uuid4().hex[:8])  # same batch name, other campus

    direct = _create_hw(client, teacher_a, student_ids=[stu_a["id"]], title="Direct")
    batch_hw = _create_hw(
        client,
        teacher_a,
        student_ids=[],
        batch_label="Morning A",
        assignment_target="entire",
        title="Batch",
    )
    _create_hw(client, teacher_b, student_ids=[], batch_label="Morning A", assignment_target="entire", title="Other campus")

    listed_a = client.get("/api/homework/student", headers=_hdr(stu_a["token"]))
    assert listed_a.status_code == 200
    ids_a = {x["id"] for x in listed_a.json()["assignments"]}
    assert direct["id"] in ids_a
    assert batch_hw["id"] in ids_a
    assert "student_ids" not in listed_a.json()["assignments"][0]

    listed_a2 = client.get("/api/homework/student", headers=_hdr(stu_a2["token"]))
    ids_a2 = {x["id"] for x in listed_a2.json()["assignments"]}
    assert batch_hw["id"] in ids_a2
    assert direct["id"] not in ids_a2

    # Same batch name other campus must not see teacher A's entire-batch HW
    listed_b = client.get("/api/homework/student", headers=_hdr(stu_b["token"]))
    ids_b = {x["id"] for x in listed_b.json()["assignments"]}
    assert batch_hw["id"] not in ids_b

    # Header spoof cannot unlock
    spoof = client.get(
        f"/api/homework/student/{direct['id']}",
        headers={**_hdr(stu_b["token"]), "X-Student-Id": stu_a["id"]},
    )
    assert spoof.status_code == 404


def test_submit_duplicate_deadline_and_idor(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m13-s-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, suffix=uuid.uuid4().hex[:8])
    other = _mk("student", branch, suffix=uuid.uuid4().hex[:8])
    past = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
    future = (datetime.utcnow() + timedelta(days=2)).isoformat() + "Z"

    late = _create_hw(client, teacher, student_ids=[stu["id"]], deadline=past, title="Late")
    assert (
        client.post(f"/api/homework/student/{late['id']}/start", headers=_hdr(stu["token"])).status_code
        == 403
    )

    hw = _create_hw(client, teacher, student_ids=[stu["id"]], deadline=future, title="On time")
    with patch(
        "app.homework.controllers._open_skill",
        new=AsyncMock(return_value={"kind": "writing-question", "questionId": "q1", "ref": "att-1"}),
    ):
        start = client.post(f"/api/homework/student/{hw['id']}/start", headers=_hdr(stu["token"]))
    assert start.status_code == 200, start.text
    sub_id = start.json()["submission_id"]

    # Reuse — second start must not spawn another open_skill if ref still "open"
    with patch(
        "app.homework.controllers._open_skill",
        new=AsyncMock(side_effect=AssertionError("should not open again")),
    ):
        # attempt_ref exists but WritingAttempt missing → will try open; patch reuse by setting kind only
        # Force reuse path via mock of _reuse_open_session
        with patch(
            "app.homework.controllers._reuse_open_session",
            return_value={"kind": "writing-question", "questionId": "q1", "ref": "att-1"},
        ):
            again = client.post(f"/api/homework/student/{hw['id']}/start", headers=_hdr(stu["token"]))
    assert again.status_code == 200
    assert again.json()["submission_id"] == sub_id

    done = client.post(
        f"/api/homework/student/submissions/{sub_id}/complete",
        headers=_hdr(stu["token"]),
        json={"estimated_band": 6.0},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "submitted"

    # Idempotent complete
    dup = client.post(
        f"/api/homework/student/submissions/{sub_id}/complete",
        headers=_hdr(stu["token"]),
        json={"estimated_band": 9.0},
    )
    assert dup.status_code == 200
    assert dup.json()["status"] == "submitted"

    # Cannot restart after submit
    assert client.post(f"/api/homework/student/{hw['id']}/start", headers=_hdr(stu["token"])).status_code == 409

    # Foreign student denied
    assert client.post(f"/api/homework/student/{hw['id']}/start", headers=_hdr(other["token"])).status_code == 404
    assert (
        client.post(
            f"/api/homework/student/submissions/{sub_id}/complete",
            headers=_hdr(other["token"]),
            json={},
        ).status_code
        == 404
    )


def test_teacher_review_scoped(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m13-r-{uuid.uuid4().hex[:6]}"
    b = f"m13-s2-{uuid.uuid4().hex[:6]}"
    teacher_a = _mk("teacher", a)
    teacher_b = _mk("teacher", b, suffix=uuid.uuid4().hex[:8])
    stu = _mk("student", a, suffix=uuid.uuid4().hex[:8])
    hw = _create_hw(client, teacher_a, student_ids=[stu["id"]])
    with patch(
        "app.homework.controllers._open_skill",
        new=AsyncMock(return_value={"kind": "writing-question", "questionId": "q", "ref": "r1"}),
    ):
        start = client.post(f"/api/homework/student/{hw['id']}/start", headers=_hdr(stu["token"])).json()
    client.post(
        f"/api/homework/student/submissions/{start['submission_id']}/complete",
        headers=_hdr(stu["token"]),
        json={},
    )

    ok = client.post(
        f"/api/homework/teacher/submissions/{start['submission_id']}/review",
        headers=_hdr(teacher_a["token"]),
        json={"teacher_band": 6.5, "teacher_comments": "Clearer examples.", "publish": True},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["teacher_band"] == 6.5

    assert (
        client.post(
            f"/api/homework/teacher/submissions/{start['submission_id']}/review",
            headers=_hdr(teacher_b["token"]),
            json={"teacher_band": 9.0},
        ).status_code
        == 403
    )

    detail = client.get(f"/api/homework/student/{hw['id']}", headers=_hdr(stu["token"]))
    assert detail.status_code == 200
    assert detail.json().get("teacher_band") == 6.5
    assert "student_ids" not in detail.json()


def test_speaking_homework_session_has_student_id(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m13-sp-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, suffix=uuid.uuid4().hex[:8])
    hw = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Speaking HW",
            "module": "speaking",
            "scope": "piece",
            "source": "manual",
            "task": "Part 1",
            "student_ids": [stu["id"]],
            "payload": {"prompt": "Do you enjoy reading?"},
        },
    )
    assert hw.status_code == 200, hw.text
    start = client.post(f"/api/homework/student/{hw.json()['id']}/start", headers=_hdr(stu["token"]))
    assert start.status_code == 200, start.text
    sid = start.json()["sessionId"]
    db = _TestSessionFactory()
    try:
        sess = db.query(TestSession).filter_by(id=sid).first()
        assert sess is not None
        assert sess.student_id == stu["id"]
    finally:
        db.close()


def test_writing_reading_listening_homework_owned_attempts(client, monkeypatch):
    """Representative IELTS links: Homework start binds attempts to JWT student."""
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from app.listening.models import ListeningAttempt
    from app.reading.models import ReadingAttempt
    from app.writing.models import WritingAttempt

    branch = f"m13-sk-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, suffix=uuid.uuid4().hex[:8])

    writing = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Write HW",
            "module": "writing",
            "scope": "piece",
            "source": "manual",
            "student_ids": [stu["id"]],
            "payload": {"prompt": "Discuss public transport.", "task_number": 2, "question_type": "opinion"},
        },
    )
    assert writing.status_code == 200, writing.text
    w_start = client.post(f"/api/homework/student/{writing.json()['id']}/start", headers=_hdr(stu["token"]))
    assert w_start.status_code == 200, w_start.text
    assert w_start.json()["kind"] == "writing-question"

    reading = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Read HW",
            "module": "reading",
            "scope": "piece",
            "source": "manual",
            "student_ids": [stu["id"]],
            "payload": {
                "passage": "Climate change affects coastal cities worldwide.",
                "questions": [
                    {
                        "id": "q1",
                        "type": "short_answer",
                        "prompt": "What is affected?",
                        "accepted_answers": ["coastal cities"],
                    }
                ],
            },
        },
    )
    assert reading.status_code == 200, reading.text
    r_start = client.post(f"/api/homework/student/{reading.json()['id']}/start", headers=_hdr(stu["token"]))
    assert r_start.status_code == 200, r_start.text
    assert r_start.json()["kind"] == "reading"

    listening = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Listen HW",
            "module": "listening",
            "scope": "piece",
            "source": "manual",
            "student_ids": [stu["id"]],
            "payload": {
                "questions": [
                    {
                        "id": "lq1",
                        "type": "short_answer",
                        "prompt": "Who is speaking?",
                        "accepted_answers": ["guide"],
                    }
                ],
            },
        },
    )
    assert listening.status_code == 200, listening.text
    l_start = client.post(f"/api/homework/student/{listening.json()['id']}/start", headers=_hdr(stu["token"]))
    assert l_start.status_code == 200, l_start.text
    assert l_start.json()["kind"] == "listening"

    db = _TestSessionFactory()
    try:
        wa = db.query(WritingAttempt).filter_by(id=w_start.json()["ref"]).first()
        ra = db.query(ReadingAttempt).filter_by(id=r_start.json()["ref"]).first()
        la = db.query(ListeningAttempt).filter_by(id=l_start.json()["ref"]).first()
        assert wa is not None and wa.student_id == stu["id"]
        assert ra is not None and ra.student_id == stu["id"]
        assert la is not None and la.student_id == stu["id"]
        detail = client.get(f"/api/homework/student/{reading.json()['id']}", headers=_hdr(stu["token"]))
        payload = detail.json().get("payload") or {}
        assert "answer_key" not in payload and "accepted_answers" not in payload
    finally:
        db.close()


def test_teacher_cannot_delete_homework_with_submissions(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m13-d-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, suffix=uuid.uuid4().hex[:8])
    hw = _create_hw(client, teacher, student_ids=[stu["id"]])
    with patch(
        "app.homework.controllers._open_skill",
        new=AsyncMock(return_value={"kind": "writing-question", "questionId": "q", "ref": "r"}),
    ):
        start = client.post(f"/api/homework/student/{hw['id']}/start", headers=_hdr(stu["token"])).json()
    client.post(
        f"/api/homework/student/submissions/{start['submission_id']}/complete",
        headers=_hdr(stu["token"]),
        json={},
    )
    assert client.delete(f"/api/homework/teacher/assignments/{hw['id']}", headers=_hdr(teacher["token"])).status_code == 409
    db = _TestSessionFactory()
    try:
        assert db.query(HomeworkAssignment).filter_by(id=hw["id"]).first() is not None
        assert db.query(HomeworkSubmission).filter_by(id=start["submission_id"]).first() is not None
    finally:
        db.close()


def test_inactive_student_denied_history_kept(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m13-i-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, suffix=uuid.uuid4().hex[:8])
    hw = _create_hw(client, teacher, student_ids=[stu["id"]])
    db = _TestSessionFactory()
    try:
        u = db.query(User).filter_by(id=stu["id"]).first()
        u.is_active = False
        db.commit()
    finally:
        db.close()
    assert client.get("/api/homework/student", headers=_hdr(stu["token"])).status_code == 403
    db = _TestSessionFactory()
    try:
        assert db.query(HomeworkAssignment).filter_by(id=hw["id"]).first() is not None
    finally:
        db.close()
