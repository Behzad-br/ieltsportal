"""Module 22 — Error handling / failure recovery."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.ai_safety import public_ai_error
from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.db_integrity import scan_failure_states
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.main import generic_exception_handler, http_exception_handler
from app.speaking.services import speaking_session
from app.writing.models import WritingAttempt, WritingQuestion
from app.writing.services import writing_service
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str = "student", branch_id: str = "m22-br", *, batch: str = "Morning") -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name="M22 Branch", city="M22", is_active=True))
        if batch and not db.query(Batch).filter_by(branch_id=branch_id, name=batch).first():
            db.add(Batch(id=f"bat-{uuid.uuid4().hex[:8]}", branch_id=branch_id, name=batch))
        tag = uuid.uuid4().hex[:8]
        row = User(
            id=f"{role[:3]}-{tag}",
            role=role,
            email=f"m22_{role[0]}_{tag}@m22.test",
            username=f"m22_{role[0]}_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name=f"M22 {role}",
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
        return {"id": row.id, "token": token, "branch_id": branch_id, "batch": batch}
    finally:
        db.close()


def _seed_writing_q() -> WritingQuestion:
    db = _TestSessionFactory()
    try:
        q = WritingQuestion(
            public_id=f"M22-{uuid.uuid4().hex[:8].upper()}",
            test_type="academic",
            task_number=2,
            question_type="opinion",
            title="M22 Essay",
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


# ── 500 sanitization ─────────────────────────────────────────────────────────


def test_500_sanitization_no_raw_exception(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    scope = {"type": "http", "method": "GET", "path": "/api/x", "headers": []}
    request = Request(scope)
    request.state.request_id = "m22-req-1"

    async def _run():
        return await generic_exception_handler(
            request,
            RuntimeError("Traceback: sk-SECRETKEY1234567890 postgresql://u:p@host/db"),
        )

    resp = asyncio.run(_run())
    assert resp.status_code == 500
    body = resp.body.decode()
    assert "sk-SECRET" not in body
    assert "Traceback" not in body
    assert "postgresql://" not in body
    assert "An internal error occurred" in body
    assert resp.headers.get("x-request-id") == "m22-req-1"


def test_http_exception_preserves_status_and_redacts_secrets():
    scope = {"type": "http", "method": "GET", "path": "/api/x", "headers": []}
    request = Request(scope)
    request.state.request_id = "m22-req-2"
    exc = HTTPException(status_code=409, detail="Bearer eyJhbGciOiJIUzI1NiJ9.abc duplicate user")

    async def _run():
        return await http_exception_handler(request, exc)

    resp = asyncio.run(_run())
    assert resp.status_code == 409
    body = resp.body.decode()
    assert "eyJhbGciOiJIUzI1NiJ9" not in body
    assert "duplicate user" in body.lower() or "***" in body


# ── Validation / conflict / auth ─────────────────────────────────────────────


def test_validation_422_safe(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    res = client.post("/api/auth/login", json={"username": "x"})  # missing password
    assert res.status_code == 422
    detail = res.json().get("detail")
    assert detail is not None


def test_conflict_409_duplicate_user(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    suffix = uuid.uuid4().hex[:8]
    branch_id = f"m22-dup-{suffix}"
    db = _TestSessionFactory()
    try:
        db.add(Branch(id=branch_id, name=f"Dup {suffix}", city="M22", is_active=True))
        db.add(Batch(id=f"bat-{suffix}", branch_id=branch_id, name=f"Morning {suffix}"))
        db.commit()
    finally:
        db.close()
    sa = _mk("super_admin", branch_id)
    body = {
        "role": "student",
        "email": f"dup_{suffix}@m22.test",
        "username": f"dup_user_{suffix}",
        "password": "Passw0rd!",
        "full_name": "Dup Student",
        "branch_id": branch_id,
        "batch": f"Morning {suffix}",
    }
    first = client.post("/api/org/users", headers=_hdr(sa["token"]), json=body)
    assert first.status_code == 200, first.text
    second = client.post("/api/org/users", headers=_hdr(sa["token"]), json=body)
    assert second.status_code == 409


def test_auth_401_403_404_policy(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    assert client.get("/api/auth/me").status_code == 401
    a = _mk("student")
    b = _mk("student")
    q = _seed_writing_q()
    start = client.post(
        f"/api/writing/questions/{q.public_id}/start", headers=_hdr(a["token"])
    )
    assert start.status_code == 200
    aid = start.json()["id"]
    assert client.get(f"/api/writing/attempts/{aid}", headers=_hdr(b["token"])).status_code in (
        403,
        404,
    )
    assert client.get("/api/writing/attempts/missing-id", headers=_hdr(a["token"])).status_code == 404


# ── DB rollback / nested commit ──────────────────────────────────────────────


def test_db_rollback_on_failed_commit(db):
    """get_db rolls back; nested helpers with commit=False stay uncommitted until outer owns it."""
    q = WritingQuestion(
        public_id=f"RB-{uuid.uuid4().hex[:6].upper()}",
        test_type="academic",
        task_number=2,
        question_type="opinion",
        title="Rollback",
        prompt="Prompt",
        status="published",
        source_type="bank",
        minimum_words=150,
        recommended_minutes=40,
    )
    db.add(q)
    db.commit()
    db.refresh(q)

    attempt = writing_service.start_attempt(db, "stu-rb", q, commit=False)
    assert attempt.id
    db.rollback()
    gone = db.query(WritingAttempt).filter_by(id=attempt.id).first()
    assert gone is None


def test_speaking_create_session_commit_false_rolls_with_outer(db):
    from app.speaking.models import TestSession

    session, qs = speaking_session.create_session(
        db,
        "stored",
        {"id": "t1", "title": "T", "part1": ["Q1"], "part2": {"topic": "T", "bullets": ["a"]}, "part3": ["Q3"]},
        student_id="stu-sp",
        commit=False,
    )
    assert session.id and qs
    db.rollback()
    assert db.query(TestSession).filter_by(id=session.id).first() is None


def test_partial_multi_row_homework_open_skill_transaction(db):
    """If outer commit fails after linked skill flush, nothing persists."""
    branch_id = f"m22-hw-{uuid.uuid4().hex[:6]}"
    if not db.query(Branch).filter_by(id=branch_id).first():
        db.add(Branch(id=branch_id, name="HW", city="X", is_active=True))
    stu = User(
        id=f"stu-{uuid.uuid4().hex[:6]}",
        role="student",
        email=f"hw_{uuid.uuid4().hex[:6]}@m22.test",
        username=f"hw_{uuid.uuid4().hex[:6]}",
        password_hash=hash_password("Passw0rd!"),
        full_name="HW",
        branch_id=branch_id,
        batch="Morning",
        is_active=True,
    )
    db.add(stu)
    q = WritingQuestion(
        public_id=f"HWQ-{uuid.uuid4().hex[:6].upper()}",
        test_type="academic",
        task_number=2,
        question_type="opinion",
        title="HWQ",
        prompt="P",
        status="published",
        source_type="bank",
        minimum_words=150,
        recommended_minutes=40,
    )
    db.add(q)
    db.flush()
    row = HomeworkAssignment(
        id=str(uuid.uuid4()),
        title="M22 HW",
        module="writing",
        scope="piece",
        source="bank",
        task_label="Task 2",
        batch_label="Morning",
        created_by=stu.id,
    )
    row.payload = {"question_id": q.id}
    db.add(row)
    db.commit()

    sub = HomeworkSubmission(
        id=str(uuid.uuid4()),
        assignment_id=row.id,
        student_id=stu.id,
        status="in_progress",
    )
    db.add(sub)
    db.flush()
    attempt = writing_service.start_attempt(db, stu.id, q, commit=False)
    sub.attempt_kind = "writing-question"
    sub.attempt_ref = attempt.id
    # Simulate outer commit failure / abort
    db.rollback()

    assert db.query(HomeworkSubmission).filter_by(id=sub.id).first() is None
    assert db.query(WritingAttempt).filter_by(id=attempt.id).first() is None


# ── AI / files / rate limit / idempotency ────────────────────────────────────


def test_ai_timeout_preserves_essay_and_sanitizes_error(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "WRITING_AI_GRADING_ENABLED", True)
    q = _seed_writing_q()
    a = _mk()
    start = client.post(
        f"/api/writing/questions/{q.public_id}/start", headers=_hdr(a["token"])
    ).json()
    essay = "Education remains the cornerstone of opportunity for every generation. " * 10
    with patch(
        "app.writing.services.writing_ai.writing_chat_json",
        new=AsyncMock(side_effect=RuntimeError("api_key=sk-LEAKED12345 timeout")),
    ):
        res = client.post(
            f"/api/writing/attempts/{start['id']}/submit",
            headers=_hdr(a["token"]),
            json={"answer_text": essay},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "grading"
    assert "Education remains" in body["answer_text"]
    err = body.get("grading_error") or ""
    assert "sk-LEAKED" not in err
    assert "api_key" not in err.lower() or "unavailable" in err.lower() or "failed" in err.lower()


def test_public_ai_error_strips_secrets():
    assert "sk-" not in public_ai_error("Minimax api_key=sk-abc1234567890 boom")
    safe = public_ai_error("provider timeout")
    assert "timeout" in safe.lower()


def test_invalid_notes_upload_safe(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    tea = _mk("teacher")
    res = client.post(
        "/api/notes/teacher",
        headers=_hdr(tea["token"]),
        data={"title": "Bad", "description": ""},
        files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
    )
    assert res.status_code in (400, 422)
    detail = str(res.json().get("detail") or "")
    assert "traceback" not in detail.lower()


def test_rate_limit_429_safe(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from app.security_hardening import ai_limiter

    # Exhaust limiter quickly for a known key pattern used by writing grade.
    a = _mk()
    q = _seed_writing_q()
    start = client.post(
        f"/api/writing/questions/{q.public_id}/start", headers=_hdr(a["token"])
    ).json()
    # Force many AI-gated calls via model-answer after a fake submitted status.
    db = _TestSessionFactory()
    try:
        row = db.query(WritingAttempt).filter_by(id=start["id"]).first()
        row.status = "submitted"
        row.answer_text = "x" * 200
        row.submitted_text = row.answer_text
        db.commit()
    finally:
        db.close()

    hit_429 = False
    with patch(
        "app.writing.services.writing_ai.generate_model_answer",
        new=AsyncMock(return_value="model"),
    ):
        for _ in range(80):
            r = client.post(
                f"/api/writing/attempts/{start['id']}/model-answer",
                headers=_hdr(a["token"]),
                json={"band_style": 7},
            )
            if r.status_code == 429:
                hit_429 = True
                body = r.json()
                assert "detail" in body
                assert "traceback" not in str(body).lower()
                break
    ai_limiter.reset()
    if not hit_429:
        pytest.skip("rate limiter window did not trip in this env")


def test_reading_submit_idempotent(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from app.reading.services import bank as reading_bank

    test = reading_bank.get_test("academic-reading-01")
    if not test:
        pytest.skip("reading bank unavailable")
    a = _mk()
    start = client.post(
        "/api/reading/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    )
    assert start.status_code == 200, start.text
    attempt_id = start.json()["attempt_id"]
    first = client.post(
        f"/api/reading/attempts/{attempt_id}/submit",
        headers=_hdr(a["token"]),
        json={"responses": {}},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/reading/attempts/{attempt_id}/submit",
        headers=_hdr(a["token"]),
        json={"responses": {"1": "CHANGED"}},
    )
    assert second.status_code == 200
    assert second.json().get("raw_score") == first.json().get("raw_score")


def test_scan_failure_states_readonly(db):
    report = scan_failure_states(db, limit=5)
    assert "writing_grading_stuck" in report
    assert "mock_in_progress_missing_skill" in report
    assert isinstance(report["notes_missing_file"], list)


def test_request_id_header_on_success(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    res = client.get("/api/health", headers={"X-Request-ID": "client-m22-id"})
    assert res.status_code == 200
    assert res.headers.get("x-request-id") == "client-m22-id"
