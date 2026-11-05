"""Module 10 — Listening lifecycle, scoring, ownership/IDOR, answer-key leakage, audio safety."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from app.auth.models import Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.listening.models import ListeningAttempt
from app.listening.services import bank
from app.listening.services.scoring import (
    PART_HIDDEN,
    QUESTION_HIDDEN,
    estimated_band,
    is_correct,
    normalize_answer,
    student_safe_test,
)
from tests.conftest import _TestSessionFactory

HIDDEN = QUESTION_HIDDEN | PART_HIDDEN


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


def _mk_student(branch_id: str = "m10-campus") -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name="M10 Campus", city="M10", is_active=True))
        uid = f"stu-{uuid.uuid4().hex[:10]}"
        uname = f"m10_{uid[-8:]}"
        row = User(
            id=uid,
            role="student",
            email=f"{uname}@m10.test",
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


def test_catalog_and_preview_hide_answer_keys(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    catalog = client.get("/api/listening/catalog")
    assert catalog.status_code == 200
    assert len(catalog.json()["tests"]) >= 5
    safe = client.get("/api/listening/tests/listening-test-01")
    assert safe.status_code == 200
    body = safe.json()
    assert body["question_count"] == 40
    assert not (_walk_keys(body) & HIDDEN)
    # Audio references present without localhost hardcoding
    part = body["parts"][0]
    assert part.get("audio_file") or part.get("audio_asset")
    blob = str(body)
    assert "localhost" not in blob.lower()


def test_audio_media_basename_and_missing(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    # Valid basename from pack
    ok = client.get("/api/listening/audio/listening-test-01-part-1.mp3")
    assert ok.status_code == 200, ok.text
    assert ok.headers.get("content-type", "").startswith("audio") or ok.content[:3] == b"ID3" or len(ok.content) > 100

    missing = client.get("/api/listening/audio/does-not-exist-m10.mp3")
    assert missing.status_code == 404

    # Path traversal / absolute-style names must not escape (basename-only)
    for bad in (
        "../listening_audio/listening-test-01-part-1.mp3",
        "..%2Flistening-test-01-part-1.mp3",
        "....//....//listening-test-01-part-1.mp3",
        "C:/Windows/win.ini",
    ):
        resp = client.get(f"/api/listening/audio/{bad}")
        # Either 404 (rejected) or 200 only if basename happens to be a real pack file
        if resp.status_code == 200:
            assert Path(bad).name in ("listening-test-01-part-1.mp3",)
        else:
            assert resp.status_code == 404


def test_start_attempt_jwt_ownership_ignores_spoofed_header(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    b = _mk_student()
    start = client.post(
        "/api/listening/attempts",
        headers={**_hdr(a["token"]), "X-Student-Id": b["id"]},
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": False},
    )
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["status"] == "in_progress"
    assert body["policy"]["label"] == "practice" or body["timed"] is False
    assert not (_walk_keys(body.get("test") or {}) & HIDDEN)
    attempt_id = body["attempt_id"]

    db = _TestSessionFactory()
    try:
        row = db.query(ListeningAttempt).filter_by(id=attempt_id).first()
        assert row is not None
        assert row.student_id == a["id"]
        assert row.student_id != b["id"]
        assert row.test_id == "listening-test-01"
        assert row.status == "in_progress"
        assert row.snapshot is not None
    finally:
        db.close()


def test_nonexistent_invalid_mode_and_repeated_starts(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    assert (
        client.post(
            "/api/listening/attempts",
            headers=_hdr(a["token"]),
            json={"test_id": "does-not-exist", "mode": "full_mock"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/listening/attempts",
            headers=_hdr(a["token"]),
            json={"test_id": "listening-test-01", "mode": "bogus"},
        ).status_code
        == 400
    )
    # Repeated starts create separate attempts (no resume-by-default policy)
    first = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": False},
    ).json()
    second = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": False},
    ).json()
    assert first["attempt_id"] != second["attempt_id"]


def test_save_resume_foreign_question_and_idor(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    b = _mk_student()
    start = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": False},
    ).json()
    attempt_id = start["attempt_id"]
    qnum = str(start["test"]["questions"][0]["number"])

    saved = client.patch(
        f"/api/listening/attempts/{attempt_id}/responses",
        headers=_hdr(a["token"]),
        json={"responses": {qnum: "riverside", "foreign-qid": "HACK", "reading-q-99": "nope"}},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["remaining_seconds"] is None  # untimed

    resumed = client.get(f"/api/listening/attempts/{attempt_id}", headers=_hdr(a["token"]))
    assert resumed.status_code == 200
    responses = resumed.json()["responses"]
    assert responses.get(qnum) == "riverside"
    assert "foreign-qid" not in responses
    assert "reading-q-99" not in responses

    assert client.get(f"/api/listening/attempts/{attempt_id}", headers=_hdr(b["token"])).status_code == 403
    assert (
        client.patch(
            f"/api/listening/attempts/{attempt_id}/responses",
            headers=_hdr(b["token"]),
            json={"responses": {qnum: "stolen"}},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/listening/attempts/{attempt_id}/submit",
            headers=_hdr(b["token"]),
            json={"responses": {}},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/listening/attempts/{attempt_id}/audio-start",
            headers=_hdr(b["token"]),
            json={"part_id": start["test"]["parts"][0]["id"]},
        ).status_code
        == 403
    )


def test_audio_playback_policy_mock_vs_practice(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    mock = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": True},
    ).json()
    assert mock["policy"]["plays_allowed"] == 1
    assert mock["policy"]["seeking_allowed"] is False
    part_id = mock["test"]["parts"][0]["id"]
    first = client.post(
        f"/api/listening/attempts/{mock['attempt_id']}/audio-start",
        headers=_hdr(a["token"]),
        json={"part_id": part_id},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/listening/attempts/{mock['attempt_id']}/audio-start",
        headers=_hdr(a["token"]),
        json={"part_id": part_id},
    )
    assert second.status_code == 403
    # Foreign part_id rejected
    assert (
        client.post(
            f"/api/listening/attempts/{mock['attempt_id']}/audio-start",
            headers=_hdr(a["token"]),
            json={"part_id": "not-a-part"},
        ).status_code
        == 400
    )

    practice = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "single_part", "part_id": part_id, "timed": False},
    ).json()
    assert practice["policy"]["seeking_allowed"] is True
    assert practice["policy"]["plays_allowed"] >= 2


def test_scoring_normalization_and_band_boundaries():
    assert normalize_answer("  Hall! ") == "hall"
    assert normalize_answer("14th May") == "14th may"
    test = bank.get_test("listening-test-01")
    multi = next(q for q in test["questions"] if q["type"] == "multiple_choice_multiple")
    assert is_correct(multi, list(reversed(multi["answer"])))
    assert not is_correct(multi, [multi["answer"][0]])

    cases = {
        0: 0.0,
        1: 1.0,
        3: 2.0,
        16: 5.0,
        23: 6.0,
        30: 7.0,
        32: 7.5,
        35: 8.0,
        37: 8.5,
        39: 9.0,
        40: 9.0,
    }
    for raw, band in cases.items():
        assert estimated_band(raw, bank.thresholds()) == band, f"listening raw={raw}"


def test_submit_deterministic_idempotent_and_persist(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    test = bank.get_test("listening-test-01")
    start = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": False},
    ).json()
    attempt_id = start["attempt_id"]
    # 32 correct by question id (server scoring path)
    responses = {}
    for i, q in enumerate(test["questions"]):
        if i < 32:
            responses[q["id"]] = q["answer"]
    first = client.post(
        f"/api/listening/attempts/{attempt_id}/submit",
        headers=_hdr(a["token"]),
        json={"responses": responses},
    )
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["raw_score"] == 32
    assert result["unanswered"] == 8
    assert result["estimated_band"] == 7.5

    second = client.post(
        f"/api/listening/attempts/{attempt_id}/submit",
        headers=_hdr(a["token"]),
        json={"responses": {test["questions"][0]["id"]: "CHANGED"}},
    )
    assert second.json()["raw_score"] == 32

    db = _TestSessionFactory()
    try:
        row = db.query(ListeningAttempt).filter_by(id=attempt_id).first()
        assert row.status == "submitted"
        assert row.raw_score == 32
        assert row.estimated_band == 7.5
        assert row.result is not None
        assert row.submitted_at is not None
    finally:
        db.close()

    own = client.get(f"/api/listening/attempts/{attempt_id}/result", headers=_hdr(a["token"]))
    assert own.status_code == 200
    assert own.json()["raw_score"] == 32

    b = _mk_student()
    assert client.get(f"/api/listening/attempts/{attempt_id}/result", headers=_hdr(b["token"])).status_code == 403

    # Submitted attempt cannot reopen as active
    reopened = client.get(f"/api/listening/attempts/{attempt_id}", headers=_hdr(a["token"]))
    assert reopened.json()["status"] == "submitted"
    assert "test" not in reopened.json() or reopened.json().get("result")


def test_history_and_progress_own_only(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    b = _mk_student()
    start = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": False},
    ).json()
    test = bank.get_test("listening-test-01")
    responses = {q["id"]: q["answer"] for q in test["questions"][:5]}
    client.post(
        f"/api/listening/attempts/{start['attempt_id']}/submit",
        headers=_hdr(a["token"]),
        json={"responses": responses},
    )
    hist_a = client.get("/api/listening/history", headers=_hdr(a["token"]))
    assert hist_a.status_code == 200
    ids_a = {row["id"] for row in hist_a.json()["attempts"]}
    assert start["attempt_id"] in ids_a
    hist_b = client.get("/api/listening/history", headers=_hdr(b["token"]))
    ids_b = {row["id"] for row in hist_b.json()["attempts"]}
    assert start["attempt_id"] not in ids_b

    prog = client.get("/api/listening/progress", headers=_hdr(a["token"]))
    assert prog.status_code == 200
    assert prog.json()["total_submitted"] >= 1


def test_timer_server_enforcement(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    start = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": True},
    ).json()
    attempt_id = start["attempt_id"]
    assert start["remaining_seconds"] is not None
    assert start["remaining_seconds"] <= start["duration_seconds"]
    assert start["policy"]["label"] == "mock"

    db = _TestSessionFactory()
    try:
        row = db.query(ListeningAttempt).filter_by(id=attempt_id).first()
        row.started_at = datetime.utcnow() - timedelta(seconds=int(row.duration_seconds) + 5)
        db.commit()
    finally:
        db.close()

    qnum = str(start["test"]["questions"][0]["number"])
    blocked = client.patch(
        f"/api/listening/attempts/{attempt_id}/responses",
        headers=_hdr(a["token"]),
        json={"responses": {qnum: "late"}},
    )
    assert blocked.status_code == 400

    # Client cannot extend via remaining_seconds body field
    # (also expired — so blocked; start a fresh timed attempt for extend check)
    fresh = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": True},
    ).json()
    rem_before = fresh["remaining_seconds"]
    patch = client.patch(
        f"/api/listening/attempts/{fresh['attempt_id']}/responses",
        headers=_hdr(a["token"]),
        json={"responses": {qnum: "ok"}, "remaining_seconds": rem_before + 99999},
    )
    assert patch.status_code == 200
    assert patch.json()["remaining_seconds"] <= rem_before

    submitted = client.post(
        f"/api/listening/attempts/{attempt_id}/submit",
        headers=_hdr(a["token"]),
        json={"responses": {qnum: "late"}},
    )
    assert submitted.status_code == 200
    assert submitted.json()["raw_score"] == 0


def test_inactive_student_denied(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = _mk_student()
    start = client.post(
        "/api/listening/attempts",
        headers=_hdr(a["token"]),
        json={"test_id": "listening-test-01", "mode": "full_mock", "timed": False},
    ).json()
    db = _TestSessionFactory()
    try:
        user = db.query(User).filter_by(id=a["id"]).first()
        user.is_active = False
        db.commit()
    finally:
        db.close()
    assert client.get(f"/api/listening/attempts/{start['attempt_id']}", headers=_hdr(a["token"])).status_code == 403


def test_student_safe_bank_unit():
    test = bank.get_test("listening-test-01")
    safe = student_safe_test(test)
    assert not (_walk_keys(safe) & HIDDEN)
    assert safe["parts"][0].get("audio_file")
