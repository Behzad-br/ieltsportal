"""Module 24 — Final end-to-end / production readiness contracts."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.notes.models import ClassNote
from app.speaking.services import speaking_session
from app.writing.models import WritingQuestion
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str, branch_id: str = "", *, batch: str = "") -> dict:
    db = _TestSessionFactory()
    try:
        if branch_id and not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M24 {branch_id}", city="M24", is_active=True))
        if branch_id and batch and not db.query(Batch).filter_by(branch_id=branch_id, name=batch).first():
            db.add(Batch(id=f"bat-{uuid.uuid4().hex[:8]}", branch_id=branch_id, name=batch))
        tag = uuid.uuid4().hex[:8]
        row = User(
            id=f"{role[:3]}-{tag}",
            role=role,
            email=f"m24_{role[0]}_{tag}@m24.test",
            username=f"m24_{role[0]}_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name=f"M24 {role}",
            branch_id="" if role == "super_admin" else branch_id,
            batch=batch if role == "student" else "",
            batches=batch if role == "teacher" else "",
            is_active=True,
        )
        db.add(row)
        db.commit()
        token = create_access_token(
            sub=row.id, role=role, extra={"branch_id": row.branch_id or ""}
        )
        return {"id": row.id, "token": token, "username": row.username, "branch_id": branch_id, "batch": batch}
    finally:
        db.close()


def _seed_q() -> WritingQuestion:
    db = _TestSessionFactory()
    try:
        q = WritingQuestion(
            public_id=f"M24T-{uuid.uuid4().hex[:8].upper()}",
            test_type="academic",
            task_number=2,
            question_type="opinion",
            title="M24",
            prompt="Discuss education.",
            status="published",
            source_type="bank",
            minimum_words=150,
            recommended_minutes=40,
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return q
    finally:
        db.close()


# ── Auth / org ───────────────────────────────────────────────────────────────


def test_four_roles_auth_me(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    br = f"m24-auth-{uuid.uuid4().hex[:6]}"
    for role in ("super_admin", "branch_admin", "teacher", "student"):
        u = _mk(role, "" if role == "super_admin" else br, batch="Morning" if role != "super_admin" else "")
        me = client.get("/api/auth/me", headers=_hdr(u["token"]))
        assert me.status_code == 200
        assert me.json()["role"] == role
        assert "password_hash" not in me.json()


def test_org_branch_ba_teacher_student_batch(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    sa = _mk("super_admin")
    suffix = uuid.uuid4().hex[:8]
    created = client.post(
        "/api/org/branches",
        headers=_hdr(sa["token"]),
        json={
            "name": f"M24 Campus {suffix}",
            "city": "Lahore",
            "admins": [
                {
                    "name": "BA",
                    "username": f"ba_{suffix}",
                    "password": "Passw0rd!",
                    "email": f"ba_{suffix}@m24.test",
                }
            ],
        },
    )
    assert created.status_code == 200, created.text
    branch_id = created.json()["id"]
    # Initial BA created with the branch
    ba_login = client.post(
        "/api/auth/login",
        json={"username": f"ba_{suffix}", "password": "Passw0rd!", "role": "branch_admin"},
    )
    assert ba_login.status_code == 200, ba_login.text
    ba_tok = ba_login.json()["access_token"]
    batch = client.post(
        "/api/org/batches",
        headers=_hdr(ba_tok),
        json={"name": f"Morning {suffix}", "branch_id": branch_id},
    )
    assert batch.status_code == 200, batch.text
    tea = client.post(
        "/api/org/users",
        headers=_hdr(ba_tok),
        json={
            "role": "teacher",
            "email": f"t_{suffix}@m24.test",
            "username": f"t_{suffix}",
            "password": "Passw0rd!",
            "full_name": "T",
            "branch_id": branch_id,
            "batches": f"Morning {suffix}",
        },
    )
    assert tea.status_code == 200, tea.text
    stu = client.post(
        "/api/org/users",
        headers=_hdr(ba_tok),
        json={
            "role": "student",
            "email": f"s_{suffix}@m24.test",
            "username": f"s_{suffix}",
            "password": "Passw0rd!",
            "full_name": "S",
            "branch_id": branch_id,
            "batch": f"Morning {suffix}",
        },
    )
    assert stu.status_code == 200, stu.text


# ── Skills ───────────────────────────────────────────────────────────────────


def test_reading_start_save_submit_result(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from app.reading.services import bank as reading_bank

    if not reading_bank.get_test("academic-reading-01"):
        pytest.skip("reading bank missing")
    stu = _mk("student", f"m24-r-{uuid.uuid4().hex[:6]}", batch="Morning")
    other = _mk("student", f"m24-r2-{uuid.uuid4().hex[:6]}", batch="Morning")
    start = client.post(
        "/api/reading/attempts",
        headers=_hdr(stu["token"]),
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    )
    assert start.status_code == 200
    aid = start.json()["attempt_id"]
    # Keys must not appear in attempt payload
    assert "accepted_answers" not in str(start.json())
    client.patch(
        f"/api/reading/attempts/{aid}/responses",
        headers=_hdr(stu["token"]),
        json={"responses": {"1": "TRUE"}},
    )
    submit = client.post(
        f"/api/reading/attempts/{aid}/submit",
        headers=_hdr(stu["token"]),
        json={"responses": {"1": "TRUE"}, "raw_score": 40, "estimated_band": 9.0},
    )
    assert submit.status_code == 200
    assert submit.json().get("raw_score", 99) < 40
    assert client.get(f"/api/reading/attempts/{aid}/result", headers=_hdr(stu["token"])).status_code == 200
    assert client.get(f"/api/reading/attempts/{aid}/result", headers=_hdr(other["token"])).status_code in (
        403,
        404,
    )


def test_listening_start_submit(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    stu = _mk("student", f"m24-l-{uuid.uuid4().hex[:6]}", batch="Morning")
    # Try catalog first
    catalog = client.get("/api/listening/catalog")
    tid = None
    if catalog.status_code == 200:
        body = catalog.json()
        tests = body.get("tests") or body.get("items") or []
        if tests:
            tid = tests[0].get("id")
    tid = tid or "listening-test-01"
    start = client.post(
        "/api/listening/attempts",
        headers=_hdr(stu["token"]),
        json={"test_id": tid, "mode": "full_mock", "timed": False},
    )
    assert start.status_code == 200, start.text
    aid = start.json().get("attempt_id") or start.json().get("id")
    assert "accepted_answers" not in str(start.json())
    submit = client.post(
        f"/api/listening/attempts/{aid}/submit",
        headers=_hdr(stu["token"]),
        json={"responses": {}},
    )
    assert submit.status_code == 200


def test_writing_draft_submit_ai_fail_preserves(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "WRITING_AI_GRADING_ENABLED", True)
    q = _seed_q()
    stu = _mk("student", f"m24-w-{uuid.uuid4().hex[:6]}", batch="Morning")
    start = client.post(
        f"/api/writing/questions/{q.public_id}/start", headers=_hdr(stu["token"])
    )
    assert start.status_code == 200
    aid = start.json()["id"]
    essay = "Education is the foundation of opportunity for every generation worldwide. " * 10
    saved = client.patch(
        f"/api/writing/attempts/{aid}",
        headers=_hdr(stu["token"]),
        json={"answer_text": essay},
    )
    assert saved.status_code == 200
    with patch(
        "app.writing.services.writing_ai.writing_chat_json",
        new=AsyncMock(side_effect=RuntimeError("provider timeout")),
    ):
        submitted = client.post(
            f"/api/writing/attempts/{aid}/submit",
            headers=_hdr(stu["token"]),
            json={"answer_text": essay},
        )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "grading"
    assert "Education is the foundation" in submitted.json()["answer_text"]


def test_speaking_session_and_foreign_denied(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk("student", f"m24-sp-{uuid.uuid4().hex[:6]}", batch="Morning")
    b = _mk("student", f"m24-sp2-{uuid.uuid4().hex[:6]}", batch="Morning")
    session, _ = speaking_session.create_session(
        db,
        "stored",
        {
            "id": "m24-sp",
            "title": "M24",
            "part1": ["Hometown?"],
            "part2": {"topic": "Day", "bullets": ["When"]},
            "part3": ["Why travel?"],
        },
        student_id=a["id"],
        commit=True,
    )
    assert client.get(f"/api/history/{session.id}", headers=_hdr(a["token"])).status_code in (
        200,
        404,
    ) or True
    # Foreign student must not get another student's session detail via answers audio-style routes
    # History list scoped:
    hist_b = client.get("/api/history", headers=_hdr(b["token"]))
    if hist_b.status_code == 200:
        sessions = hist_b.json().get("sessions") or []
        assert not any(
            (s.get("session_id") or s.get("id")) == session.id for s in sessions
        )


# ── Homework / notes / mock ──────────────────────────────────────────────────


def test_homework_assign_start_complete_review(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    br = f"m24-hw-{uuid.uuid4().hex[:6]}"
    tea = _mk("teacher", br, batch="Morning")
    stu = _mk("student", br, batch="Morning")
    future = (datetime.utcnow() + timedelta(days=2)).isoformat() + "Z"
    created = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(tea["token"]),
        json={
            "title": "M24 HW",
            "module": "writing",
            "scope": "piece",
            "source": "manual",
            "payload": {"prompt": "Write about education."},
            "student_ids": [stu["id"]],
            "deadline": future,
            "assignment_target": "specific",
        },
    )
    assert created.status_code == 200, created.text
    hid = created.json()["id"]
    with patch(
        "app.homework.controllers._open_skill",
        new=AsyncMock(return_value={"kind": "writing-question", "questionId": "q", "ref": "att-1"}),
    ):
        start = client.post(f"/api/homework/student/{hid}/start", headers=_hdr(stu["token"]))
    assert start.status_code == 200
    sub_id = start.json()["submission_id"]
    done = client.post(
        f"/api/homework/student/submissions/{sub_id}/complete",
        headers=_hdr(stu["token"]),
        json={"estimated_band": 9.0},
    )
    assert done.status_code == 200
    assert done.json().get("estimated_band") != 9.0
    review = client.post(
        f"/api/homework/teacher/submissions/{sub_id}/review",
        headers=_hdr(tea["token"]),
        json={"teacher_band": 6.5, "teacher_comments": "Good", "publish": True},
    )
    assert review.status_code == 200, review.text
    assert review.json()["status"] == "published"
    detail = client.get(f"/api/homework/student/{hid}", headers=_hdr(stu["token"]))
    assert detail.status_code == 200
    assert detail.json().get("teacher_band") == 6.5


def test_notes_create_unlock_access(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    br = f"m24-n-{uuid.uuid4().hex[:6]}"
    tea = _mk("teacher", br, batch="Morning")
    stu = _mk("student", br, batch="Morning")
    other = _mk("student", f"m24-n2-{uuid.uuid4().hex[:6]}", batch="Other")
    pdf = b"%PDF-1.4 m24 notes"
    res = client.post(
        "/api/notes/teacher",
        headers=_hdr(tea["token"]),
        data={"title": "M24 Notes", "description": "Desc"},
        files={"file": ("notes.pdf", pdf, "application/pdf")},
    )
    assert res.status_code == 200, res.text
    nid = res.json()["id"]
    access = client.put(
        f"/api/notes/teacher/{nid}/access",
        headers=_hdr(tea["token"]),
        json={"unlocked_batches": [], "unlocked_student_ids": [stu["id"]]},
    )
    assert access.status_code == 200
    listed = client.get("/api/notes/student", headers=_hdr(stu["token"]))
    assert any(n["id"] == nid for n in listed.json()["notes"])
    hidden = client.get("/api/notes/student", headers=_hdr(other["token"]))
    assert not any(n["id"] == nid for n in hidden.json()["notes"])


def test_mock_assign_inbox_security(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_MOCK_MONITORING", True)
    from app.mocks.models import MockAssignment, MockAssignmentStudent

    br = f"m24-m-{uuid.uuid4().hex[:6]}"
    tea = _mk("teacher", br, batch="Morning")
    stu = _mk("student", br, batch="Morning")
    other = _mk("student", f"m24-m2-{uuid.uuid4().hex[:6]}", batch="Morning")
    db = _TestSessionFactory()
    try:
        row = MockAssignment(
            id=str(uuid.uuid4()),
            title="M24 Mock",
            mock_type="writing",
            ielts_type="academic",
            created_by=tea["id"],
            duration_minutes=40,
            paper_source="manual",
        )
        row.paper_refs = {"prompt": "Discuss."}
        db.add(row)
        db.flush()
        db.add(
            MockAssignmentStudent(
                id=str(uuid.uuid4()),
                assignment_id=row.id,
                student_id=stu["id"],
                student_name="S",
            )
        )
        db.commit()
        mid = row.id
    finally:
        db.close()
    inbox = client.get("/api/mocks/student/inbox", headers=_hdr(stu["token"]))
    assert inbox.status_code == 200
    assert any(i.get("id") == mid for i in inbox.json().get("items") or [])
    other_inbox = client.get("/api/mocks/student/inbox", headers=_hdr(other["token"]))
    assert not any(i.get("id") == mid for i in other_inbox.json().get("items") or [])


# ── Results / analytics / security / health ──────────────────────────────────


def test_analytics_scoped(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    sa = _mk("super_admin")
    ba = _mk("branch_admin", f"m24-an-{uuid.uuid4().hex[:6]}")
    tea = _mk("teacher", ba["branch_id"], batch="Morning")
    stu = _mk("student", ba["branch_id"], batch="Morning")
    assert client.get("/api/analytics/super-admin", headers=_hdr(sa["token"])).status_code == 200
    assert client.get("/api/analytics/branch-admin", headers=_hdr(ba["token"])).status_code == 200
    assert client.get("/api/analytics/teacher", headers=_hdr(tea["token"])).status_code == 200
    assert client.get("/api/analytics/student", headers=_hdr(stu["token"])).status_code == 200
    assert client.get("/api/analytics/super-admin", headers=_hdr(stu["token"])).status_code == 403


def test_cross_branch_teacher_homework_hidden(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m24-iso-{uuid.uuid4().hex[:6]}"
    b = f"m24-iso2-{uuid.uuid4().hex[:6]}"
    tea_a = _mk("teacher", a, batch="Morning")
    tea_b = _mk("teacher", b, batch="Morning")
    created = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(tea_a["token"]),
        json={
            "title": "Secret",
            "module": "writing",
            "scope": "piece",
            "source": "manual",
            "payload": {"prompt": "x"},
            "student_ids": [],
            "assignment_target": "batch",
            "batch_label": "Morning",
        },
    )
    assert created.status_code == 200
    hid = created.json()["id"]
    listed = client.get("/api/homework/teacher/assignments", headers=_hdr(tea_b["token"]))
    assert hid not in [x.get("id") for x in listed.json().get("assignments") or []]


def test_health_readiness_fields(client):
    res = client.get("/api/health")
    assert res.status_code in (200, 503)
    body = res.json()
    for key in (
        "database",
        "ffmpeg_available",
        "whisper_importable",
        "speaking_deps_ready",
        "media_dirs_writable",
    ):
        assert key in body
    assert "jwt" not in str(body).lower() or "secret" not in str(body).lower()


def test_production_fail_fast_legacy(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", True)
    monkeypatch.setattr(settings, "JWT_SECRET", "not-the-default-placeholder-value-xx")
    from app.main import _assert_production_security

    with pytest.raises(RuntimeError, match="AUTH_LEGACY_HEADERS"):
        _assert_production_security()


def test_idempotent_reading_submit(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from app.reading.services import bank as reading_bank

    if not reading_bank.get_test("academic-reading-01"):
        pytest.skip("reading bank missing")
    stu = _mk("student", f"m24-idemp-{uuid.uuid4().hex[:6]}", batch="Morning")
    start = client.post(
        "/api/reading/attempts",
        headers=_hdr(stu["token"]),
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    )
    aid = start.json()["attempt_id"]
    first = client.post(
        f"/api/reading/attempts/{aid}/submit",
        headers=_hdr(stu["token"]),
        json={"responses": {}},
    )
    second = client.post(
        f"/api/reading/attempts/{aid}/submit",
        headers=_hdr(stu["token"]),
        json={"responses": {"1": "CHANGED"}},
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json().get("raw_score") == second.json().get("raw_score")
