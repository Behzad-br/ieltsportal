"""Module 16 — Results ownership, AI vs final, band validation, mock labeling."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.ielts_bands import compute_overall_band, is_valid_ielts_band, parse_ielts_band, single_skill_result_label
from app.mocks.models import MockAttempt
from app.writing.models import WritingAttempt, WritingQuestion
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str, branch_id: str, *, batch: str | None = None, suffix: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M16 {branch_id}", city="M16", is_active=True))
        tag = suffix or uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"m16_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m16.test",
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
        return {"id": row.id, "token": token, "branch_id": branch_id}
    finally:
        db.close()


def test_band_helpers():
    assert is_valid_ielts_band(6.5)
    assert not is_valid_ielts_band(9.5)
    assert not is_valid_ielts_band(-1)
    assert compute_overall_band(7.0, 7.0, 6.5, 6.5) == 7.0
    assert single_skill_result_label("writing") == "Writing band"
    assert parse_ielts_band(6.25, field="teacher_band") == 6.5
    from fastapi import HTTPException

    try:
        parse_ielts_band(12, field="teacher_band")
        assert False, "expected 400"
    except HTTPException as exc:
        assert exc.status_code == 400


def test_writing_ai_vs_teacher_and_idor(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m16-a-{uuid.uuid4().hex[:6]}"
    b = f"m16-b-{uuid.uuid4().hex[:6]}"
    stu_a = _mk("student", a, batch="Morning")
    stu_b = _mk("student", b, batch="Morning")
    tea_a = _mk("teacher", a)
    tea_b = _mk("teacher", b)

    q = WritingQuestion(
        public_id=f"M16-{uuid.uuid4().hex[:6]}",
        test_type="academic",
        task_number=2,
        question_type="opinion",
        prompt="Discuss education.",
        minimum_words=250,
        recommended_minutes=40,
        status="published",
        source_type="original_generated",
    )
    db.add(q)
    db.commit()
    db.refresh(q)

    start = client.post(
        f"/api/writing/questions/{q.public_id}/start",
        headers=_hdr(stu_a["token"]),
    )
    assert start.status_code == 200, start.text
    aid = start.json()["id"]
    client.patch(
        f"/api/writing/attempts/{aid}",
        headers=_hdr(stu_a["token"]),
        json={"answer_text": "Education is important for society for many reasons. " * 20},
    )
    # Force AI estimate without calling paid AI
    row = db.query(WritingAttempt).filter_by(id=aid).first()
    row.estimated_band = 6.0
    row.final_band = 6.0
    row.status = "graded"
    db.commit()

    own = client.get(f"/api/writing/attempts/{aid}", headers=_hdr(stu_a["token"]))
    assert own.status_code == 200
    assert own.json()["estimated_band"] == 6.0
    assert own.json()["band_source"] in ("ai_estimated", "teacher", "none")

    denied = client.get(f"/api/writing/attempts/{aid}", headers=_hdr(stu_b["token"]))
    assert denied.status_code in (403, 404)

    # Invalid teacher band
    bad = client.post(
        f"/api/writing/admin/submissions/{aid}/review",
        headers=_hdr(tea_a["token"]),
        json={"teacher_band": 12, "publish": True},
    )
    assert bad.status_code == 400

    ok = client.post(
        f"/api/writing/admin/submissions/{aid}/review",
        headers=_hdr(tea_a["token"]),
        json={"teacher_band": 6.5, "publish": True},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json().get("final_band") == 6.5 or ok.json().get("teacher_band") == 6.5

    # Foreign teacher denied
    foreign = client.post(
        f"/api/writing/admin/submissions/{aid}/review",
        headers=_hdr(tea_b["token"]),
        json={"teacher_band": 9, "publish": True},
    )
    assert foreign.status_code in (403, 404)

    refreshed = client.get(f"/api/writing/attempts/{aid}", headers=_hdr(stu_a["token"]))
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body.get("teacher_band") == 6.5
    assert body.get("estimated_band") == 6.0  # AI preserved
    assert body.get("band_source") == "teacher"
    assert body.get("final_band") == 6.5


def test_mock_single_skill_result_label(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m16-m-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Morning")
    q = WritingQuestion(
        public_id=f"M16M-{uuid.uuid4().hex[:6]}",
        test_type="academic",
        task_number=2,
        question_type="opinion",
        prompt="Discuss transport.",
        minimum_words=250,
        recommended_minutes=40,
        status="published",
        source_type="original_generated",
    )
    db.add(q)
    db.commit()
    db.refresh(q)

    created = client.post(
        "/api/mocks/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Writing result label",
            "mock_type": "writing",
            "result_mode": "ai",
            "paper_refs": {"question_id": q.id},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu["id"], "name": "S"}],
        },
    )
    assert created.status_code == 200, created.text
    aid = created.json()["id"]
    start = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(stu["token"]),
        json={"assignment_id": aid, "consent": True},
    )
    assert start.status_code == 200, start.text
    attempt_id = start.json()["attempt_id"]
    ref = start.json()["skill"].get("ref")
    wa = db.query(WritingAttempt).filter_by(id=ref).first()
    if wa:
        wa.estimated_band = 6.5
        db.commit()
    submit = client.post(f"/api/mocks/attempts/{attempt_id}/submit", headers=_hdr(stu["token"]), json={})
    assert submit.status_code == 200
    detail = client.get(f"/api/mocks/student/{aid}", headers=_hdr(stu["token"]))
    assert detail.status_code == 200
    body = detail.json()
    assert body.get("result_label") == "Writing band"
    assert body.get("skill_band") == body.get("overall_band") or body.get("skill_band") == 6.5


def test_homework_invalid_teacher_band(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from unittest.mock import AsyncMock, patch

    branch = f"m16-h-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Morning")
    hw = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "HW band",
            "module": "writing",
            "source": "manual",
            "payload": {"prompt": "Write."},
            "student_ids": [stu["id"]],
        },
    )
    assert hw.status_code == 200
    with patch(
        "app.homework.controllers._open_skill",
        new=AsyncMock(return_value={"kind": "writing-question", "questionId": "q", "ref": "r"}),
    ):
        start = client.post(f"/api/homework/student/{hw.json()['id']}/start", headers=_hdr(stu["token"]))
    assert start.status_code == 200
    sub_id = start.json()["submission_id"]
    client.post(f"/api/homework/student/submissions/{sub_id}/complete", headers=_hdr(stu["token"]), json={})
    bad = client.post(
        f"/api/homework/teacher/submissions/{sub_id}/review",
        headers=_hdr(teacher["token"]),
        json={"teacher_band": 99, "publish": True},
    )
    assert bad.status_code == 400


def test_homework_teacher_band_hidden_until_published(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    from unittest.mock import AsyncMock, patch

    branch = f"m16-hp-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Morning")
    hw = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "HW publish gate",
            "module": "writing",
            "source": "manual",
            "payload": {"prompt": "Write."},
            "student_ids": [stu["id"]],
        },
    )
    assert hw.status_code == 200
    aid = hw.json()["id"]
    with patch(
        "app.homework.controllers._open_skill",
        new=AsyncMock(return_value={"kind": "writing-question", "questionId": "q", "ref": "r"}),
    ):
        start = client.post(f"/api/homework/student/{aid}/start", headers=_hdr(stu["token"]))
    sub_id = start.json()["submission_id"]
    client.post(f"/api/homework/student/submissions/{sub_id}/complete", headers=_hdr(stu["token"]), json={})

    draft = client.post(
        f"/api/homework/teacher/submissions/{sub_id}/review",
        headers=_hdr(teacher["token"]),
        json={"teacher_band": 6.5, "teacher_comments": "Draft only", "publish": False},
    )
    assert draft.status_code == 200
    assert draft.json()["status"] == "reviewed"

    pre = client.get(f"/api/homework/student/{aid}", headers=_hdr(stu["token"]))
    assert pre.status_code == 200
    assert pre.json().get("teacher_band") is None
    assert pre.json().get("teacher_comments") is None
    assert pre.json().get("result_pending") is True

    pub = client.post(
        f"/api/homework/teacher/submissions/{sub_id}/review",
        headers=_hdr(teacher["token"]),
        json={"teacher_band": 6.5, "teacher_comments": "Published", "publish": True},
    )
    assert pub.status_code == 200
    assert pub.json()["status"] == "published"

    post = client.get(f"/api/homework/student/{aid}", headers=_hdr(stu["token"]))
    assert post.status_code == 200
    assert post.json().get("teacher_band") == 6.5
    assert post.json().get("teacher_comments") == "Published"
    assert post.json().get("band_source") == "teacher"


def test_mock_submit_hides_unpublished_band(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m16-ms-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Morning")
    q = WritingQuestion(
        public_id=f"M16S-{uuid.uuid4().hex[:6]}",
        test_type="academic",
        task_number=2,
        question_type="opinion",
        prompt="Discuss cities.",
        minimum_words=250,
        recommended_minutes=40,
        status="published",
        source_type="original_generated",
    )
    db.add(q)
    db.commit()
    db.refresh(q)

    created = client.post(
        "/api/mocks/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "Teacher-mode unpublished",
            "mock_type": "writing",
            "result_mode": "teacher",
            "paper_refs": {"question_id": q.id},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu["id"], "name": "S"}],
        },
    )
    assert created.status_code == 200, created.text
    aid = created.json()["id"]
    start = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(stu["token"]),
        json={"assignment_id": aid, "consent": True},
    )
    assert start.status_code == 200, start.text
    attempt_id = start.json()["attempt_id"]
    ref = start.json()["skill"].get("ref")
    wa = db.query(WritingAttempt).filter_by(id=ref).first()
    if wa:
        wa.estimated_band = 6.0
        db.commit()

    submit = client.post(f"/api/mocks/attempts/{attempt_id}/submit", headers=_hdr(stu["token"]), json={})
    assert submit.status_code == 200
    body = submit.json()
    assert body.get("published") is False
    assert body.get("overall_band") is None
    assert body.get("result_pending") is True

    detail = client.get(f"/api/mocks/student/{aid}", headers=_hdr(stu["token"]))
    assert detail.status_code == 200
    assert detail.json().get("overall_band") is None
    assert detail.json().get("published") is False
