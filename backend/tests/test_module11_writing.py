"""Module 11 — Writing lifecycle, AI validation, teacher scope, WRITING_ADMIN_TOKEN."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.models import Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.writing.models import WritingAttempt, WritingQuestion
from app.writing.services.writing_core import (
    coerce_band,
    resolve_writing_file,
    validate_grading_payload,
)
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_user(role: str = "student", branch_id: str = "m11-campus", suffix: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M11 {branch_id}", city="M11", is_active=True))
        tag = suffix or uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"m11_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m11.test",
            username=uname,
            password_hash=hash_password("Passw0rd!"),
            full_name=uname,
            branch_id=branch_id if role != "super_admin" else None,
            is_active=True,
        )
        db.add(row)
        db.commit()
        token = create_access_token(
            sub=row.id,
            role=role,
            extra={"branch_id": branch_id} if role != "super_admin" else {},
        )
        return {"id": row.id, "token": token, "username": uname, "branch_id": branch_id}
    finally:
        db.close()


def _seed_q(
    *,
    public_id: str | None = None,
    status: str = "published",
    task_number: int = 2,
    question_type: str = "agree_disagree",
    prompt: str = "Some people believe education is the most important factor.",
    image_path: str | None = None,
) -> WritingQuestion:
    db = _TestSessionFactory()
    try:
        q = WritingQuestion(
            id=str(uuid.uuid4()),
            public_id=public_id or f"M11-{uuid.uuid4().hex[:8].upper()}",
            test_type="academic",
            task_number=task_number,
            question_type=question_type,
            topic="education",
            prompt=prompt,
            minimum_words=150 if task_number == 1 else 250,
            recommended_minutes=20 if task_number == 1 else 40,
            status=status,
            is_permanent_bank=True,
            image_path=image_path,
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return q
    finally:
        db.close()


def _grading_payload(overall: float = 6.0, criteria_band: float | None = None) -> dict:
    b = criteria_band if criteria_band is not None else overall
    return {
        "estimated_overall_band": overall,
        "criteria": {
            "task_response_or_achievement": {"band": b, "feedback": "ok"},
            "coherence_and_cohesion": {"band": b, "feedback": "ok"},
            "lexical_resource": {"band": b, "feedback": "ok"},
            "grammatical_range_and_accuracy": {"band": b, "feedback": "ok"},
        },
        "strengths": ["clear"],
        "priority_improvements": ["examples"],
        "why_this_band": ["balanced"],
        "next_steps": ["practice"],
    }


def test_catalog_requires_auth_and_hides_unpublished(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    # No JWT
    assert client.get("/api/writing/catalog").status_code in (401, 403)
    pub = _seed_q(public_id="M11-PUB", status="published")
    draft = _seed_q(public_id="M11-DRF", status="draft", prompt="Hidden draft prompt unique xyz")
    stu = _mk_user()
    cat = client.get("/api/writing/catalog", headers=_hdr(stu["token"]), params={"test_type": "academic"})
    assert cat.status_code == 200, cat.text
    blob = str(cat.json())
    assert pub.public_id in blob or pub.id in blob or "education" in blob.lower()
    assert "Hidden draft prompt unique xyz" not in blob
    assert draft.public_id not in blob


def test_unpublished_image_not_served(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    draft = _seed_q(status="draft", task_number=1, question_type="bar_chart", image_path="missing.png")
    assert client.get(f"/api/writing/images/{draft.id}").status_code == 404


def test_resolve_writing_file_blocks_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WRITING_IMAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "WRITING_PACKS_DIR", str(tmp_path / "packs"))
    (tmp_path / "packs").mkdir()
    safe = tmp_path / "ok.png"
    safe.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert resolve_writing_file("ok.png") is not None
    assert resolve_writing_file("../ok.png") is None
    # Absolute path outside roots must not resolve even if file exists
    outside = tmp_path.parent / f"outside-{uuid.uuid4().hex}.png"
    outside.write_bytes(b"secret")
    try:
        assert resolve_writing_file(str(outside)) is None
    finally:
        outside.unlink(missing_ok=True)


def test_draft_save_resume_and_idor(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    q = _seed_q()
    a = _mk_user(suffix=uuid.uuid4().hex[:8])
    b = _mk_user(suffix=uuid.uuid4().hex[:8])
    start = client.post(
        f"/api/writing/questions/{q.public_id}/start",
        headers={**_hdr(a["token"]), "X-Student-Id": b["id"]},
    )
    assert start.status_code == 200, start.text
    attempt_id = start.json()["id"]
    db = _TestSessionFactory()
    try:
        row = db.query(WritingAttempt).filter_by(id=attempt_id).first()
        assert row.student_id == a["id"]
    finally:
        db.close()

    saved = client.patch(
        f"/api/writing/attempts/{attempt_id}",
        headers=_hdr(a["token"]),
        json={"answer_text": "Draft sentence about education and society."},
    )
    assert saved.status_code == 200
    assert saved.json()["status"] == "saved"

    resumed = client.get(f"/api/writing/attempts/{attempt_id}", headers=_hdr(a["token"]))
    assert "education and society" in resumed.json()["answer_text"]

    assert client.get(f"/api/writing/attempts/{attempt_id}", headers=_hdr(b["token"])).status_code == 403
    assert (
        client.patch(
            f"/api/writing/attempts/{attempt_id}",
            headers=_hdr(b["token"]),
            json={"answer_text": "stolen"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/writing/attempts/{attempt_id}/submit",
            headers=_hdr(b["token"]),
            json={"answer_text": "stolen"},
        ).status_code
        == 403
    )


def test_submit_idempotent_ai_failure_preserves_essay(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "WRITING_AI_GRADING_ENABLED", True)
    q = _seed_q()
    a = _mk_user()
    start = client.post(f"/api/writing/questions/{q.public_id}/start", headers=_hdr(a["token"])).json()
    essay = "Education is vital for social mobility and national development. " * 12

    with patch(
        "app.writing.services.writing_ai.writing_chat_json",
        new=AsyncMock(side_effect=RuntimeError("provider timeout")),
    ):
        first = client.post(
            f"/api/writing/attempts/{start['id']}/submit",
            headers=_hdr(a["token"]),
            json={"answer_text": essay},
        )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "grading"
    assert body["grading_error"]
    assert "Education is vital" in body["answer_text"]

    db = _TestSessionFactory()
    try:
        row = db.query(WritingAttempt).filter_by(id=start["id"]).first()
        assert row.submitted_text and "Education is vital" in row.submitted_text
        assert row.status == "grading"
    finally:
        db.close()

    # Duplicate submit must not overwrite with a different essay
    second = client.post(
        f"/api/writing/attempts/{start['id']}/submit",
        headers=_hdr(a["token"]),
        json={"answer_text": "CHANGED AFTER FAILURE"},
    )
    assert second.status_code == 200
    assert "CHANGED AFTER FAILURE" not in second.json()["answer_text"]
    db = _TestSessionFactory()
    try:
        row = db.query(WritingAttempt).filter_by(id=start["id"]).first()
        assert "CHANGED AFTER FAILURE" not in (row.submitted_text or "")
    finally:
        db.close()


def test_submit_grades_and_teacher_override(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "WRITING_AI_GRADING_ENABLED", True)
    monkeypatch.setattr(settings, "WRITING_ADMIN_TOKEN", "legacy-m11")
    q = _seed_q()
    branch = f"m11-br-{uuid.uuid4().hex[:6]}"
    stu = _mk_user(branch_id=branch)
    teacher = _mk_user(role="teacher", branch_id=branch, suffix=uuid.uuid4().hex[:8])
    other = _mk_user(role="teacher", branch_id=f"m11-other-{uuid.uuid4().hex[:6]}", suffix=uuid.uuid4().hex[:8])

    start = client.post(f"/api/writing/questions/{q.public_id}/start", headers=_hdr(stu["token"])).json()
    essay = "I strongly agree that education transforms communities through knowledge. " * 10
    with patch(
        "app.writing.services.writing_ai.writing_chat_json",
        new=AsyncMock(return_value=_grading_payload(6.0)),
    ):
        submitted = client.post(
            f"/api/writing/attempts/{start['id']}/submit",
            headers=_hdr(stu["token"]),
            json={"answer_text": essay},
        )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "graded"
    assert submitted.json()["estimated_band"] == 6.0

    # Teacher JWT works without admin token
    review = client.post(
        f"/api/writing/admin/submissions/{start['id']}/review",
        headers=_hdr(teacher["token"]),
        json={"teacher_band": 6.5, "teacher_comments": "Clearer examples.", "publish": True},
    )
    assert review.status_code == 200, review.text
    assert review.json()["teacher_band"] == 6.5

    # Cross-branch teacher denied
    assert (
        client.post(
            f"/api/writing/admin/submissions/{start['id']}/review",
            headers=_hdr(other["token"]),
            json={"teacher_band": 9.0},
        ).status_code
        == 403
    )

    detail = client.get(f"/api/writing/attempts/{start['id']}", headers=_hdr(stu["token"])).json()
    assert detail["band_source"] == "teacher"
    assert detail["final_band"] == 6.5
    assert detail["estimated_band"] == 6.0


def test_grading_payload_rejects_malformed_and_derives_overall():
    with pytest.raises(ValueError):
        validate_grading_payload({"criteria": {}}, task_number=2)
    with pytest.raises(ValueError):
        coerce_band(12.0)
    with pytest.raises(ValueError):
        coerce_band(-1)
    with pytest.raises(ValueError):
        coerce_band("excellent")

    # Model claims 9.0 but criteria average to 6.0 → criterion average wins
    parsed = validate_grading_payload(
        {
            "estimated_overall_band": 9.0,
            "criteria": {
                "task_response_or_achievement": {"band": 6.0, "feedback": ""},
                "coherence_and_cohesion": {"band": 6.0, "feedback": ""},
                "lexical_resource": {"band": 6.0, "feedback": ""},
                "grammatical_range_and_accuracy": {"band": 6.0, "feedback": ""},
            },
        },
        task_number=1,
    )
    assert parsed["estimated_overall_band"] == 6.0
    assert parsed["task_number"] == 1


def test_mock_rejects_unpublished_question_ids(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    draft = _seed_q(status="draft", task_number=1, question_type="bar_chart", prompt="Draft chart prompt")
    pub2 = _seed_q(task_number=2)
    stu = _mk_user()
    bad = client.post(
        "/api/writing/mock-tests",
        headers=_hdr(stu["token"]),
        json={"test_type": "academic", "task1_id": draft.id, "task2_id": pub2.id},
    )
    assert bad.status_code == 404


def test_admin_assignments_branch_scoped(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "WRITING_ADMIN_TOKEN", "legacy-m11")
    branch_a = f"m11-aa-{uuid.uuid4().hex[:6]}"
    branch_b = f"m11-bb-{uuid.uuid4().hex[:6]}"
    teacher_a = _mk_user(role="teacher", branch_id=branch_a, suffix=uuid.uuid4().hex[:8])
    teacher_b = _mk_user(role="teacher", branch_id=branch_b, suffix=uuid.uuid4().hex[:8])
    stu_a = _mk_user(branch_id=branch_a, suffix=uuid.uuid4().hex[:8])
    stu_b = _mk_user(branch_id=branch_b, suffix=uuid.uuid4().hex[:8])
    q = _seed_q()

    create_a = client.post(
        "/api/writing/admin/assignments",
        headers=_hdr(teacher_a["token"]),
        json={"title": "A homework", "student_id": stu_a["id"], "question_id": q.id},
    )
    assert create_a.status_code == 200, create_a.text
    create_b = client.post(
        "/api/writing/admin/assignments",
        headers=_hdr(teacher_b["token"]),
        json={"title": "B homework", "student_id": stu_b["id"], "question_id": q.id},
    )
    assert create_b.status_code == 200

    # Cross-branch assign denied
    assert (
        client.post(
            "/api/writing/admin/assignments",
            headers=_hdr(teacher_a["token"]),
            json={"title": "Steal", "student_id": stu_b["id"], "question_id": q.id},
        ).status_code
        == 403
    )

    listed = client.get("/api/writing/admin/assignments", headers=_hdr(teacher_a["token"]))
    assert listed.status_code == 200
    titles = {row["title"] for row in listed.json()["assignments"]}
    assert "A homework" in titles
    assert "B homework" not in titles


def test_teacher_jwt_without_admin_token_lists_submissions(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "WRITING_ADMIN_TOKEN", "should-not-be-required")
    branch = f"m11-jwt-{uuid.uuid4().hex[:6]}"
    teacher = _mk_user(role="teacher", branch_id=branch, suffix=uuid.uuid4().hex[:8])
    listed = client.get("/api/writing/admin/submissions", headers=_hdr(teacher["token"]))
    assert listed.status_code == 200
    # Wrong token alone still works as ops (documented residual) but JWT path is preferred
    ops = client.get("/api/writing/admin/submissions", headers={"X-Admin-Token": "should-not-be-required"})
    assert ops.status_code == 200


def test_history_own_only(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "WRITING_AI_GRADING_ENABLED", True)
    q = _seed_q()
    a = _mk_user(suffix=uuid.uuid4().hex[:8])
    b = _mk_user(suffix=uuid.uuid4().hex[:8])
    start = client.post(f"/api/writing/questions/{q.public_id}/start", headers=_hdr(a["token"])).json()
    with patch(
        "app.writing.services.writing_ai.writing_chat_json",
        new=AsyncMock(return_value=_grading_payload(5.5)),
    ):
        client.post(
            f"/api/writing/attempts/{start['id']}/submit",
            headers=_hdr(a["token"]),
            json={"answer_text": "Practice essay about education systems worldwide. " * 12},
        )
    hist_a = client.get("/api/writing/history", headers=_hdr(a["token"]))
    assert hist_a.status_code == 200
    ids_a = {row.get("id") or row.get("attempt_id") for row in hist_a.json().get("attempts") or hist_a.json().get("items") or []}
    # history shape may nest attempts differently — also check progress
    if not ids_a:
        # fallback: list attempts endpoint
        listed = client.get("/api/writing/attempts", headers=_hdr(a["token"]))
        ids_a = {row["id"] for row in listed.json()["attempts"]}
    assert start["id"] in ids_a
    listed_b = client.get("/api/writing/attempts", headers=_hdr(b["token"]))
    ids_b = {row["id"] for row in listed_b.json()["attempts"]}
    assert start["id"] not in ids_b


def test_regrade_branch_scoped(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "WRITING_AI_GRADING_ENABLED", True)
    branch = f"m11-rg-{uuid.uuid4().hex[:6]}"
    other = f"m11-rg2-{uuid.uuid4().hex[:6]}"
    stu = _mk_user(branch_id=branch)
    teacher = _mk_user(role="teacher", branch_id=branch, suffix=uuid.uuid4().hex[:8])
    foreign = _mk_user(role="teacher", branch_id=other, suffix=uuid.uuid4().hex[:8])
    q = _seed_q()
    start = client.post(f"/api/writing/questions/{q.public_id}/start", headers=_hdr(stu["token"])).json()
    with patch(
        "app.writing.services.writing_ai.writing_chat_json",
        new=AsyncMock(return_value=_grading_payload(5.0)),
    ):
        client.post(
            f"/api/writing/attempts/{start['id']}/submit",
            headers=_hdr(stu["token"]),
            json={"answer_text": "Regrade target essay with sufficient length for evaluation. " * 12},
        )
    with patch(
        "app.writing.services.writing_ai.writing_chat_json",
        new=AsyncMock(return_value=_grading_payload(6.5)),
    ):
        ok = client.post(
            f"/api/writing/admin/submissions/{start['id']}/regrade",
            headers=_hdr(teacher["token"]),
        )
    assert ok.status_code == 200, ok.text
    assert ok.json()["estimated_band"] == 6.5
    assert (
        client.post(
            f"/api/writing/admin/submissions/{start['id']}/regrade",
            headers=_hdr(foreign["token"]),
        ).status_code
        == 403
    )
