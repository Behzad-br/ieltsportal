"""Module 9 — Reading lifecycle, scoring, ownership/IDOR, answer-key leakage."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.auth.models import Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.reading.models import ReadingAttempt
from app.reading.services import bank
from app.reading.services.scoring import SERVER_ONLY_FIELDS, estimated_band, student_safe_test
from tests.conftest import _TestSessionFactory


def _walk_keys(obj):
    keys = set()
    if isinstance(obj, dict):
        keys.update(obj.keys())
        for value in obj.values():
            keys.update(_walk_keys(value))
    elif isinstance(obj, list):
        for item in obj:
            keys.update(_walk_keys(item))
    return keys


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_student(branch_id: str = "m9-campus") -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name="M9 Campus", city="M9", is_active=True))
        uid = f"stu-{uuid.uuid4().hex[:10]}"
        uname = f"m9_{uid[-8:]}"
        row = User(
            id=uid,
            role="student",
            email=f"{uname}@m9.test",
            username=uname,
            password_hash=hash_password("Passw0rd!"),
            full_name=uname,
            branch_id=branch_id,
            is_active=True,
        )
        db.add(row)
        db.commit()
        token = create_access_token(sub=row.id, role="student", extra={"branch_id": branch_id})
        return {"id": row.id, "token": token, "username": uname}
    finally:
        db.close()


def test_catalog_hides_answer_keys(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    catalog = client.get("/api/reading/catalog")
    assert catalog.status_code == 200
    assert len(catalog.json()["academic"]) >= 5
    safe = client.get("/api/reading/tests/academic-reading-01")
    assert safe.status_code == 200
    assert not (_walk_keys(safe.json()) & SERVER_ONLY_FIELDS)


def test_start_attempt_jwt_identity_and_persistence(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    b = _mk_student()
    start = client.post(
        "/api/reading/attempts",
        headers={**_hdr(a["token"]), "X-Student-Id": b["id"]},
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    )
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["status"] == "in_progress"
    assert not (_walk_keys(body.get("test") or {}) & SERVER_ONLY_FIELDS)
    attempt_id = body["attempt_id"]

    db = _TestSessionFactory()
    try:
        row = db.query(ReadingAttempt).filter_by(id=attempt_id).first()
        assert row is not None
        assert row.student_id == a["id"]
        assert row.student_id != b["id"]
        assert row.test_id == "academic-reading-01"
        assert row.status == "in_progress"
    finally:
        db.close()


def test_save_resume_foreign_keys_and_idor(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    b = _mk_student()
    start = client.post(
        "/api/reading/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    ).json()
    attempt_id = start["attempt_id"]
    qnum = str(start["test"]["questions"][0]["number"])

    saved = client.patch(
        f"/api/reading/attempts/{attempt_id}/responses",
        headers=_hdr(a["token"]),
        json={"responses": {qnum: "TRUE", "foreign-qid": "HACK"}},
    )
    assert saved.status_code == 200, saved.text

    resumed = client.get(f"/api/reading/attempts/{attempt_id}", headers=_hdr(a["token"]))
    assert resumed.status_code == 200
    assert resumed.json()["responses"].get(qnum) == "TRUE"
    assert "foreign-qid" not in resumed.json()["responses"]

    assert client.get(f"/api/reading/attempts/{attempt_id}", headers=_hdr(b["token"])).status_code == 403
    assert (
        client.patch(
            f"/api/reading/attempts/{attempt_id}/responses",
            headers=_hdr(b["token"]),
            json={"responses": {qnum: "FALSE"}},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/reading/attempts/{attempt_id}/submit",
            headers=_hdr(b["token"]),
            json={"responses": {}},
        ).status_code
        == 403
    )


def test_scoring_band_submit_idempotent(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    test = bank.get_test("academic-reading-01")
    start = client.post(
        "/api/reading/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    ).json()
    attempt_id = start["attempt_id"]
    # 32 correct answers
    responses = {}
    for i, q in enumerate(test["questions"]):
        if i < 32:
            responses[str(q["number"])] = q["accepted_answers"][0]
    first = client.post(
        f"/api/reading/attempts/{attempt_id}/submit",
        headers=_hdr(a["token"]),
        json={"responses": responses},
    )
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["raw_score"] == 32
    assert result["correct"] == 32
    assert result["unanswered"] == 8
    expected_band = estimated_band(32, bank.thresholds_for("academic"))
    assert expected_band == 7.0  # academic table: 30→7.0, 33→7.5
    assert result["estimated_band"] == expected_band

    # Duplicate submit must not mutate
    second = client.post(
        f"/api/reading/attempts/{attempt_id}/submit",
        headers=_hdr(a["token"]),
        json={"responses": {str(test["questions"][0]["number"]): "WRONG"}},
    )
    assert second.json()["raw_score"] == 32

    db = _TestSessionFactory()
    try:
        row = db.query(ReadingAttempt).filter_by(id=attempt_id).first()
        assert row.status == "submitted"
        assert row.raw_score == 32
        assert row.estimated_band == expected_band
        assert row.result is not None
        assert row.submitted_at is not None
    finally:
        db.close()

    own = client.get(f"/api/reading/attempts/{attempt_id}/result", headers=_hdr(a["token"]))
    assert own.status_code == 200
    assert own.json()["raw_score"] == 32

    b = _mk_student()
    assert client.get(f"/api/reading/attempts/{attempt_id}/result", headers=_hdr(b["token"])).status_code == 403


def test_band_boundaries():
    academic = bank.thresholds_for("academic")
    general = bank.thresholds_for("general_training")
    # Matches bank scoring.academic_reading thresholds (product source of truth).
    cases = {
        0: 0.0,
        10: 4.0,
        15: 5.0,
        20: 5.5,
        23: 6.0,
        26: 6.0,  # 23→6.0, 27→6.5
        30: 7.0,
        32: 7.0,  # 30→7.0, 33→7.5
        35: 8.0,
        37: 8.5,
        39: 9.0,
        40: 9.0,
    }
    for raw, band in cases.items():
        assert estimated_band(raw, academic) == band, f"academic raw={raw}"
    assert estimated_band(23, general) == 5.0
    assert estimated_band(32, general) == 6.5
    assert estimated_band(40, general) == 9.0


def test_history_own_only(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    b = _mk_student()
    start = client.post(
        "/api/reading/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    ).json()
    test = bank.get_test("academic-reading-01")
    responses = {str(q["number"]): q["accepted_answers"][0] for q in test["questions"][:5]}
    client.post(
        f"/api/reading/attempts/{start['attempt_id']}/submit",
        headers=_hdr(a["token"]),
        json={"responses": responses},
    )
    hist_a = client.get("/api/reading/history", headers=_hdr(a["token"]))
    assert hist_a.status_code == 200
    ids_a = {row["id"] for row in hist_a.json()["attempts"]}
    assert start["attempt_id"] in ids_a
    hist_b = client.get("/api/reading/history", headers=_hdr(b["token"]))
    ids_b = {row["id"] for row in hist_b.json()["attempts"]}
    assert start["attempt_id"] not in ids_b


def test_timer_server_enforcement(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    start = client.post(
        "/api/reading/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": True},
    ).json()
    attempt_id = start["attempt_id"]
    assert start["remaining_seconds"] is not None
    assert start["remaining_seconds"] <= start["duration_seconds"]

    # Backdate started_at so attempt is expired
    db = _TestSessionFactory()
    try:
        row = db.query(ReadingAttempt).filter_by(id=attempt_id).first()
        row.started_at = datetime.utcnow() - timedelta(seconds=int(row.duration_seconds) + 5)
        db.commit()
    finally:
        db.close()

    qnum = str(start["test"]["questions"][0]["number"])
    blocked = client.patch(
        f"/api/reading/attempts/{attempt_id}/responses",
        headers=_hdr(a["token"]),
        json={"responses": {qnum: "TRUE"}},
    )
    assert blocked.status_code == 400

    # Late body edits ignored on expired submit
    submitted = client.post(
        f"/api/reading/attempts/{attempt_id}/submit",
        headers=_hdr(a["token"]),
        json={"responses": {qnum: "TRUE"}},
    )
    assert submitted.status_code == 200
    assert submitted.json()["raw_score"] == 0


def test_inactive_student_denied(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    start = client.post(
        "/api/reading/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    ).json()
    db = _TestSessionFactory()
    try:
        user = db.query(User).filter_by(id=a["id"]).first()
        user.is_active = False
        db.commit()
    finally:
        db.close()
    assert client.get(f"/api/reading/attempts/{start['attempt_id']}", headers=_hdr(a["token"])).status_code == 403


def test_nonexistent_and_invalid_start(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    assert (
        client.post(
            "/api/reading/attempts",
            headers=_hdr(a["token"]),
            json={"test_id": "does-not-exist", "mode": "full_mock"},
        ).status_code
        == 404
    )
    assert client.get("/api/reading/attempts/missing", headers=_hdr(a["token"])).status_code == 404
