"""Module 17 — Progress / Analytics ownership, nulls, scope, averages."""
from __future__ import annotations

import uuid
from datetime import datetime

from app.analytics.bands import average_bands, effective_result_band, sanitize_band
from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.reading.models import ReadingAttempt
from app.writing.models import WritingAttempt, WritingQuestion
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str, branch_id: str, *, batch: str | None = None, suffix: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M17 {branch_id}", city="M17", is_active=True))
        tag = suffix or uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"m17_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m17.test",
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


def test_band_helpers_null_safe():
    assert sanitize_band(None) is None
    assert sanitize_band(12) is None
    assert sanitize_band(0) == 0.0
    assert sanitize_band(6.5) == 6.5
    assert average_bands([]) is None
    assert average_bands([None, None]) is None
    assert average_bands([6.0, 7.0]) == 6.5
    assert effective_result_band(teacher_band=6.5, estimated_band=6.0) == 6.5
    assert effective_result_band(estimated_band=6.0) == 6.0
    assert effective_result_band() is None


def test_student_own_analytics_and_idor(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m17-a-{uuid.uuid4().hex[:6]}"
    b = f"m17-b-{uuid.uuid4().hex[:6]}"
    stu_a = _mk("student", a, batch="Morning")
    stu_b = _mk("student", b, batch="Morning")

    # No attempts → null averages, not 0
    own = client.get("/api/analytics/student", headers=_hdr(stu_a["token"]))
    assert own.status_code == 200, own.text
    body = own.json()
    assert body["skills"]["reading"]["average_band"] is None
    assert body["skills"]["writing"]["average_band"] is None
    assert body["skills"]["overall_ielts"] is None

    db.add(
        ReadingAttempt(
            id=str(uuid.uuid4()),
            student_id=stu_a["id"],
            test_id="t1",
            test_type="academic",
            mode="practice",
            status="submitted",
            estimated_band=6.5,
            raw_score=30,
            started_at=datetime.utcnow(),
            submitted_at=datetime.utcnow(),
        )
    )
    db.commit()

    refreshed = client.get("/api/analytics/student", headers=_hdr(stu_a["token"]))
    assert refreshed.status_code == 200
    assert refreshed.json()["skills"]["reading"]["average_band"] == 6.5

    # Student B cannot see Student A via progress endpoints
    denied = client.get(
        "/api/reading/progress",
        headers={**_hdr(stu_b["token"]), "X-Student-Id": stu_a["id"]},
    )
    assert denied.status_code == 200
    # Identity ignores foreign X-Student-Id for students — B sees own empty progress
    assert denied.json()["average_band"] is None or denied.json().get("total_submitted", 0) == 0


def test_writing_teacher_final_preferred_in_progress(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m17-w-{uuid.uuid4().hex[:6]}"
    stu = _mk("student", branch, batch="Morning")
    q = WritingQuestion(
        public_id=f"M17-{uuid.uuid4().hex[:6]}",
        test_type="academic",
        task_number=2,
        question_type="opinion",
        prompt="Discuss.",
        minimum_words=250,
        recommended_minutes=40,
        status="published",
        source_type="original_generated",
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    db.add(
        WritingAttempt(
            id=str(uuid.uuid4()),
            question_id=q.id,
            student_id=stu["id"],
            status="graded",
            estimated_band=6.0,
            teacher_band=6.5,
            final_band=6.5,
            answer_text="x" * 300,
            word_count=300,
            submitted_at=datetime.utcnow(),
            started_at=datetime.utcnow(),
        )
    )
    db.commit()

    prog = client.get("/api/writing/progress", headers=_hdr(stu["token"]))
    assert prog.status_code == 200
    data = prog.json()
    assert data.get("average_band") == 6.5
    assert data.get("average_estimated_band") == 6.0
    assert data.get("band_policy") == "teacher_final_preferred"

    dash = client.get("/api/analytics/student", headers=_hdr(stu["token"]))
    assert dash.json()["skills"]["writing"]["average_band"] == 6.5


def test_teacher_branch_scope(client, monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m17-ta-{uuid.uuid4().hex[:6]}"
    b = f"m17-tb-{uuid.uuid4().hex[:6]}"
    tea_a = _mk("teacher", a)
    tea_b = _mk("teacher", b)
    stu_a = _mk("student", a, batch="Morning")
    _mk("student", b, batch="Morning")

    asn = HomeworkAssignment(
        id=str(uuid.uuid4()),
        title="M17 HW",
        module="writing",
        scope="piece",
        source="manual",
        created_by=tea_a["id"],
    )
    asn.student_ids = [stu_a["id"]]
    db.add(asn)
    db.commit()
    db.add(
        HomeworkSubmission(
            id=str(uuid.uuid4()),
            assignment_id=asn.id,
            student_id=stu_a["id"],
            status="submitted",
            submitted_at=datetime.utcnow(),
        )
    )
    db.commit()

    dash_a = client.get("/api/analytics/teacher", headers=_hdr(tea_a["token"]))
    assert dash_a.status_code == 200
    assert dash_a.json()["active_students"] >= 1
    assert dash_a.json()["pending_reviews"] >= 1
    assert dash_a.json()["branch_id"] == a

    dash_b = client.get("/api/analytics/teacher", headers=_hdr(tea_b["token"]))
    assert dash_b.status_code == 200
    assert dash_b.json()["branch_id"] == b
    assert dash_b.json()["pending_reviews"] == 0

    # Foreign student record denied
    denied = client.get(
        f"/api/analytics/student-record/{stu_a['id']}",
        headers=_hdr(tea_b["token"]),
    )
    assert denied.status_code in (403, 404)


def test_branch_admin_and_super_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m17-ba-{uuid.uuid4().hex[:6]}"
    b = f"m17-bb-{uuid.uuid4().hex[:6]}"
    ba_a = _mk("branch_admin", a)
    ba_b = _mk("branch_admin", b)
    _mk("student", a, batch="Morning")
    _mk("teacher", a)

    # Super admin
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id="m17-sa-hq").first():
            db.add(Branch(id="m17-sa-hq", name="M17 HQ", city="M17", is_active=True))
        sa = User(
            id=f"sa-{uuid.uuid4().hex[:8]}",
            role="super_admin",
            email=f"sa_{uuid.uuid4().hex[:6]}@m17.test",
            username=f"sa_{uuid.uuid4().hex[:6]}",
            password_hash=hash_password("Passw0rd!"),
            full_name="SA",
            is_active=True,
        )
        db.add(sa)
        db.commit()
        sa_token = create_access_token(sub=sa.id, role="super_admin")
        sa_id = sa.id
    finally:
        db.close()

    ba = client.get("/api/analytics/branch-admin", headers=_hdr(ba_a["token"]))
    assert ba.status_code == 200
    assert ba.json()["branch_id"] == a
    assert ba.json()["active_students"] >= 1
    assert ba.json()["active_teachers"] >= 1

    # BA cannot use teacher endpoint
    assert client.get("/api/analytics/teacher", headers=_hdr(ba_a["token"])).status_code == 403
    # BA cannot use SA endpoint
    assert client.get("/api/analytics/super-admin", headers=_hdr(ba_a["token"])).status_code == 403
    # Cross-branch student record
    stu_on_a = client.get("/api/analytics/branch-admin", headers=_hdr(ba_b["token"]))
    assert stu_on_a.json()["branch_id"] == b

    sa_dash = client.get("/api/analytics/super-admin", headers=_hdr(sa_token))
    assert sa_dash.status_code == 200
    assert sa_dash.json()["branches_total"] >= 1
    assert "active_students" in sa_dash.json()
    assert sa_dash.json().get("skill_average_band") is None or isinstance(
        sa_dash.json().get("skill_average_band"), (int, float)
    )

    # Cleanup SA user
    db = _TestSessionFactory()
    try:
        u = db.query(User).filter_by(id=sa_id).first()
        if u:
            db.delete(u)
            db.commit()
    finally:
        db.close()


def test_student_cannot_access_staff_analytics(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m17-st-{uuid.uuid4().hex[:6]}"
    stu = _mk("student", branch, batch="Morning")
    assert client.get("/api/analytics/teacher", headers=_hdr(stu["token"])).status_code == 403
    assert client.get("/api/analytics/branch-admin", headers=_hdr(stu["token"])).status_code == 403
    assert client.get("/api/analytics/super-admin", headers=_hdr(stu["token"])).status_code == 403
