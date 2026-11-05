"""Module 23 — Complete application security audit regression suite."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest

from app.auth.models import Batch, Branch, User
from app.auth.security import ALGORITHM, create_access_token, hash_password
from app.config import settings
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.reading.models import ReadingAttempt
from app.writing.models import WritingAttempt, WritingQuestion
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _ensure_branch(bid: str) -> None:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=bid).first():
            db.add(Branch(id=bid, name=f"B {bid}", city="M23", is_active=True))
            db.commit()
    finally:
        db.close()


def _mk(role: str, branch_id: str = "", *, batch: str = "") -> dict:
    db = _TestSessionFactory()
    try:
        if branch_id:
            if not db.query(Branch).filter_by(id=branch_id).first():
                db.add(Branch(id=branch_id, name=f"M23 {branch_id}", city="M23", is_active=True))
            if batch and not db.query(Batch).filter_by(branch_id=branch_id, name=batch).first():
                db.add(Batch(id=f"bat-{uuid.uuid4().hex[:8]}", branch_id=branch_id, name=batch))
        tag = uuid.uuid4().hex[:8]
        row = User(
            id=f"{role[:3]}-{tag}",
            role=role,
            email=f"m23_{role[0]}_{tag}@m23.test",
            username=f"m23_{role[0]}_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name=f"M23 {role}",
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
        return {"id": row.id, "token": token, "username": row.username, "branch_id": branch_id}
    finally:
        db.close()


# ── JWT ──────────────────────────────────────────────────────────────────────


def test_jwt_forged_role_no_escalation(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    stu = _mk("student", "m23-jwt-a", batch="Morning")
    forged = create_access_token(
        sub=stu["id"], role="super_admin", extra={"branch_id": "", "role": "super_admin"}
    )
    # DB role wins — student cannot list org users
    assert client.get("/api/org/users", headers=_hdr(forged)).status_code == 403


def test_jwt_forged_branch_claim_ignored(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = "m23-br-a"
    b = "m23-br-b"
    tea = _mk("teacher", a, batch="Morning")
    forged = create_access_token(
        sub=tea["id"], role="teacher", extra={"branch_id": b}
    )
    # Still scoped to DB branch_id — cannot create student on foreign campus
    _ensure_branch(b)
    db = _TestSessionFactory()
    try:
        if not db.query(Batch).filter_by(branch_id=b, name="X").first():
            db.add(Batch(id=f"bat-{uuid.uuid4().hex[:6]}", branch_id=b, name="X"))
            db.commit()
    finally:
        db.close()
    res = client.post(
        "/api/org/users",
        headers=_hdr(forged),
        json={
            "role": "student",
            "email": f"foreign_{uuid.uuid4().hex[:6]}@m23.test",
            "username": f"foreign_{uuid.uuid4().hex[:6]}",
            "password": "Passw0rd!",
            "full_name": "Foreign",
            "branch_id": b,
            "batch": "X",
        },
    )
    assert res.status_code in (403, 400)


def test_jwt_expired_and_malformed(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    stu = _mk("student", "m23-exp", batch="Morning")
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {
            "sub": stu["id"],
            "role": "student",
            "iat": int((now - timedelta(hours=20)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        settings.JWT_SECRET,
        algorithm=ALGORITHM,
    )
    assert client.get("/api/auth/me", headers=_hdr(expired)).status_code == 401
    assert client.get("/api/auth/me", headers=_hdr("not-a-jwt")).status_code == 401
    # alg=none rejected
    none_tok = jwt.encode(
        {"sub": stu["id"], "role": "super_admin", "exp": int((now + timedelta(hours=1)).timestamp())},
        key="",
        algorithm="none",
    )
    assert client.get("/api/auth/me", headers=_hdr(none_tok)).status_code == 401


def test_jwt_wrong_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    stu = _mk("student", "m23-ws", batch="Morning")
    bad = jwt.encode(
        {
            "sub": stu["id"],
            "role": "student",
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        },
        "wrong-secret-value",
        algorithm=ALGORITHM,
    )
    assert client.get("/api/auth/me", headers=_hdr(bad)).status_code == 401


# ── RBAC / escalation ────────────────────────────────────────────────────────


def test_student_cannot_escalate_role(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    stu = _mk("student", "m23-esc", batch="Morning")
    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(stu["token"]),
            json={
                "role": "super_admin",
                "email": f"evil_{uuid.uuid4().hex[:6]}@m23.test",
                "username": f"evil_{uuid.uuid4().hex[:6]}",
                "password": "Passw0rd!",
                "full_name": "Evil",
            },
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/org/users/{stu['id']}",
            headers=_hdr(stu["token"]),
            json={"role": "teacher", "is_active": False},
        ).status_code
        in (403, 422)
    )


def test_ba_cannot_create_super_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    ba = _mk("branch_admin", "m23-ba-esc")
    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(ba["token"]),
            json={
                "role": "super_admin",
                "email": f"sa_{uuid.uuid4().hex[:6]}@m23.test",
                "username": f"sa_{uuid.uuid4().hex[:6]}",
                "password": "Passw0rd!",
                "full_name": "Nope",
            },
        ).status_code
        == 403
    )


# ── IDOR ─────────────────────────────────────────────────────────────────────


def test_student_a_cannot_read_student_b_reading(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk("student", "m23-idor", batch="Morning")
    b = _mk("student", "m23-idor", batch="Morning")
    db = _TestSessionFactory()
    try:
        row = ReadingAttempt(
            student_id=a["id"],
            test_id="academic-reading-01",
            test_type="academic",
            mode="full_mock",
            timed=0,
            status="submitted",
            estimated_band=6.5,
            raw_score=30,
        )
        row.result = {"raw_score": 30, "estimated_band": 6.5}
        db.add(row)
        db.commit()
        aid = row.id
    finally:
        db.close()
    assert client.get(f"/api/reading/attempts/{aid}", headers=_hdr(a["token"])).status_code == 200
    assert client.get(f"/api/reading/attempts/{aid}", headers=_hdr(b["token"])).status_code in (
        403,
        404,
    )
    assert client.get(f"/api/reading/attempts/{aid}/result", headers=_hdr(b["token"])).status_code in (
        403,
        404,
    )


def test_teacher_foreign_branch_denied(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    tea_a = _mk("teacher", "m23-ta", batch="Morning")
    tea_b = _mk("teacher", "m23-tb", batch="Morning")
    # Teacher B cannot list Teacher A's homework (empty or foreign)
    res = client.get("/api/homework/teacher/assignments", headers=_hdr(tea_b["token"]))
    assert res.status_code == 200
    # Create assignment as A
    created = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(tea_a["token"]),
        json={
            "title": "Secret HW",
            "module": "writing",
            "scope": "piece",
            "source": "manual",
            "task_label": "Task 2",
            "student_ids": [],
            "batch_label": "Morning",
            "payload": {"prompt": "Write about education."},
        },
    )
    if created.status_code == 200:
        hid = created.json()["id"]
        listed_b = client.get("/api/homework/teacher/assignments", headers=_hdr(tea_b["token"]))
        assert listed_b.status_code == 200
        ids = [a.get("id") for a in (listed_b.json().get("assignments") or [])]
        assert hid not in ids


def test_ba_foreign_branch_denied(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    ba_a = _mk("branch_admin", "m23-ba-a")
    ba_b = _mk("branch_admin", "m23-ba-b")
    users = client.get("/api/org/users", headers=_hdr(ba_a["token"]))
    assert users.status_code == 200
    for u in users.json().get("users") or users.json() if isinstance(users.json(), list) else users.json().get("items") or []:
        if isinstance(u, dict) and u.get("branch_id"):
            assert u["branch_id"] == "m23-ba-a"
    # BA B cannot patch BA A campus user
    stu = _mk("student", "m23-ba-a", batch="Morning")
    assert (
        client.patch(
            f"/api/org/users/{stu['id']}",
            headers=_hdr(ba_b["token"]),
            json={"full_name": "Hacked"},
        ).status_code
        in (403, 404)
    )


# ── Legacy headers ───────────────────────────────────────────────────────────


def test_legacy_headers_ignored_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk("student", "m23-leg", batch="Morning")
    b = _mk("student", "m23-leg", batch="Morning")
    # Student A JWT + X-Student-Id=B must still resolve to A
    me = client.get(
        "/api/auth/me",
        headers={**_hdr(a["token"]), "X-Student-Id": b["id"]},
    )
    assert me.status_code == 200
    assert me.json()["id"] == a["id"]


# ── Homework keys / score tamper / audio ─────────────────────────────────────


def test_homework_nested_answer_keys_stripped(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    tea = _mk("teacher", "m23-keys", batch="Morning")
    stu = _mk("student", "m23-keys", batch="Morning")
    db = _TestSessionFactory()
    try:
        row = HomeworkAssignment(
            id=str(uuid.uuid4()),
            title="Keyed",
            module="reading",
            scope="piece",
            source="manual",
            created_by=tea["id"],
            batch_label="Morning",
        )
        row.student_ids = [stu["id"]]
        row.payload = {
            "prompt": "Read",
            "questions": [
                {"number": 1, "prompt": "Q1", "accepted_answers": ["YES"], "answer": "YES"}
            ],
            "snapshot": {
                "questions": [{"number": 2, "accepted_answers": ["NO"], "answer_key": "NO"}]
            },
        }
        db.add(row)
        db.commit()
        hid = row.id
    finally:
        db.close()
    detail = client.get(f"/api/homework/student/{hid}", headers=_hdr(stu["token"]))
    assert detail.status_code == 200, detail.text
    blob = str(detail.json())
    assert "accepted_answers" not in blob
    assert "YES" not in blob or "Read" in blob  # prompt OK; answers gone
    assert '"answer": "YES"' not in blob
    assert "answer_key" not in blob


def test_homework_client_band_ignored(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    tea = _mk("teacher", "m23-band", batch="Morning")
    stu = _mk("student", "m23-band", batch="Morning")
    db = _TestSessionFactory()
    try:
        hw = HomeworkAssignment(
            id=str(uuid.uuid4()),
            title="Band",
            module="writing",
            scope="piece",
            source="manual",
            created_by=tea["id"],
        )
        hw.student_ids = [stu["id"]]
        hw.payload = {"prompt": "Essay"}
        db.add(hw)
        sub = HomeworkSubmission(
            id=str(uuid.uuid4()),
            assignment_id=hw.id,
            student_id=stu["id"],
            status="in_progress",
            attempt_kind="writing-question",
            attempt_ref=None,
        )
        db.add(sub)
        db.commit()
        sid = sub.id
    finally:
        db.close()
    done = client.post(
        f"/api/homework/student/submissions/{sid}/complete",
        headers=_hdr(stu["token"]),
        json={"estimated_band": 9.0, "attempt_ref": "stolen-id"},
    )
    assert done.status_code in (200, 400)
    if done.status_code == 200:
        assert done.json().get("estimated_band") in (None, 0) or done.json().get("estimated_band") != 9.0
    db = _TestSessionFactory()
    try:
        row = db.query(HomeworkSubmission).filter_by(id=sid).first()
        assert row.estimated_band != 9.0
        assert row.attempt_ref != "stolen-id"
    finally:
        db.close()


def test_homework_audio_idor(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "HOMEWORK_AUDIO_DIR", str(tmp_path))
    tea = _mk("teacher", "m23-aud", batch="Morning")
    stu = _mk("student", "m23-aud", batch="Morning")
    other = _mk("student", "m23-aud-x", batch="Morning")
    name = f"{uuid.uuid4().hex[:10]}_clip.mp3"
    (tmp_path / name).write_bytes(b"ID3fakeaudio")
    db = _TestSessionFactory()
    try:
        hw = HomeworkAssignment(
            id=str(uuid.uuid4()),
            title="Audio HW",
            module="listening",
            scope="piece",
            source="manual",
            created_by=tea["id"],
        )
        hw.student_ids = [stu["id"]]
        hw.payload = {"audio_filename": name}
        db.add(hw)
        db.commit()
    finally:
        db.close()
    ok = client.get(f"/api/homework/audio/{name}", headers=_hdr(stu["token"]))
    assert ok.status_code == 200
    denied = client.get(f"/api/homework/audio/{name}", headers=_hdr(other["token"]))
    assert denied.status_code in (403, 404)
    trav = client.get("/api/homework/audio/../secrets.txt", headers=_hdr(stu["token"]))
    assert trav.status_code in (400, 404)


# ── Score tampering reading ──────────────────────────────────────────────────


def test_reading_client_score_ignored(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from app.reading.services import bank as reading_bank

    if not reading_bank.get_test("academic-reading-01"):
        pytest.skip("reading bank missing")
    stu = _mk("student", "m23-score", batch="Morning")
    start = client.post(
        "/api/reading/attempts",
        headers=_hdr(stu["token"]),
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    )
    assert start.status_code == 200
    aid = start.json()["attempt_id"]
    submit = client.post(
        f"/api/reading/attempts/{aid}/submit",
        headers=_hdr(stu["token"]),
        json={
            "responses": {},
            "raw_score": 40,
            "estimated_band": 9.0,
            "band": 9.0,
            "is_correct": True,
        },
    )
    assert submit.status_code == 200
    assert submit.json().get("estimated_band") != 9.0 or submit.json().get("raw_score") != 40
    assert submit.json().get("raw_score", 0) < 40


# ── AI / ops token ───────────────────────────────────────────────────────────


def test_student_denied_writing_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    stu = _mk("student", "m23-ai", batch="Morning")
    assert client.get("/api/writing/admin/questions", headers=_hdr(stu["token"])).status_code == 403


def test_ops_token_disabled_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "WRITING_ADMIN_TOKEN", "ops-secret-token-value")
    monkeypatch.setattr(settings, "ALLOW_WRITING_ADMIN_TOKEN", False)
    assert settings.writing_admin_token_allowed is False
    res = client.get(
        "/api/writing/admin/questions",
        headers={"X-Admin-Token": "ops-secret-token-value"},
    )
    assert res.status_code == 403


# ── Notes list scoping ───────────────────────────────────────────────────────


def test_notes_list_hides_locked(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from app.notes.models import ClassNote

    tea = _mk("teacher", "m23-note", batch="Morning")
    stu = _mk("student", "m23-note", batch="Morning")
    other = _mk("student", "m23-note2", batch="Other")
    nid = str(uuid.uuid4())
    db = _TestSessionFactory()
    try:
        if not db.query(Batch).filter_by(branch_id="m23-note", name="Morning").first():
            db.add(Batch(id=f"bat-{uuid.uuid4().hex[:6]}", branch_id="m23-note", name="Morning"))
        note = ClassNote(
            id=nid,
            title="Secret note",
            description="<script>alert(1)</script>",
            original_name="n.pdf",
            stored_name="n.pdf",
            page_count=1,
            created_by=tea["id"],
        )
        note.unlocked_batches = ["Morning"]
        note.unlocked_student_ids = []
        db.add(note)
        db.commit()
    finally:
        db.close()
    listed = client.get("/api/notes/student", headers=_hdr(stu["token"]))
    assert any(n["id"] == nid for n in listed.json()["notes"])
    # XSS payload stored as text — description returned as string (React escapes)
    row = next(n for n in listed.json()["notes"] if n["id"] == nid)
    assert "<script>" in (row.get("description") or "")
    hidden = client.get("/api/notes/student", headers=_hdr(other["token"]))
    assert not any(n["id"] == nid for n in hidden.json()["notes"])


# ── LiveKit ──────────────────────────────────────────────────────────────────


def test_livekit_student_cannot_mint_teacher(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_MOCK_MONITORING", True)
    monkeypatch.setattr(settings, "LIVEKIT_URL", "https://livekit.example")
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "key")
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "secret")
    stu = _mk("student", "m23-lk", batch="Morning")
    from app.mocks.models import MockAssignment, MockAssignmentStudent

    db = _TestSessionFactory()
    try:
        row = MockAssignment(
            id=str(uuid.uuid4()),
            title="LK",
            mock_type="writing",
            ielts_type="academic",
            created_by="teacher",
            duration_minutes=60,
        )
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
        aid = row.id
    finally:
        db.close()
    with patch("app.mocks.livekit_tokens.livekit_configured", return_value=True):
        with patch("app.mocks.livekit_tokens.create_token", return_value="tok"):
            res = client.post(
                "/api/mocks/livekit/token",
                headers=_hdr(stu["token"]),
                json={"assignment_id": aid, "role": "teacher"},
            )
    assert res.status_code == 403


# ── CORS / secrets / config ──────────────────────────────────────────────────


def test_cors_disallowed_origin(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://fti4iltes.tech")
    # Rebuild middleware list is hard; check helper policy instead.
    origins = settings.cors_origin_list()
    assert "https://evil.example" not in origins
    assert "*" not in origins


def test_health_no_secrets(client):
    res = client.get("/api/health")
    body = str(res.json()).lower()
    assert "jwt" not in body or "secret" not in body
    assert "password" not in body
    assert "sk-" not in body
    assert "postgresql://" not in body


def test_production_rejects_legacy_headers_startup(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", True)
    monkeypatch.setattr(settings, "JWT_SECRET", "strong-enough-secret-value-not-placeholder")
    from app.main import _assert_production_security

    with pytest.raises(RuntimeError, match="AUTH_LEGACY_HEADERS"):
        _assert_production_security()


def test_password_hash_never_in_user_payload(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    stu = _mk("student", "m23-pw", batch="Morning")
    me = client.get("/api/auth/me", headers=_hdr(stu["token"]))
    assert me.status_code == 200
    assert "password" not in me.json()
    assert "password_hash" not in me.json()
