"""Controlled Neon verification for Module 15 — Mock Exams."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Batch, Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.mocks.models import MockAssignment, MockAttempt, MockIntegrityEvent
from app.writing.models import WritingAttempt, WritingQuestion


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch_a = f"m15-neon-a-{tag}"
    branch_b = f"m15-neon-b-{tag}"
    ids: list[str] = []
    assign_ids: list[str] = []
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print(f"  FAIL: {msg}")
        else:
            print(f"  OK: {msg}")

    client = TestClient(app)
    db = SessionLocal()
    try:
        print("=== Neon mock integrity snapshot ===")
        attempts = db.query(MockAttempt).count()
        assigns = db.query(MockAssignment).count()
        print(f"  assignments={assigns} attempts={attempts}")
        orphan = 0
        for att in db.query(MockAttempt).limit(200).all():
            if not att.student_id:
                orphan += 1
        print(f"  attempts_missing_student={orphan}")

        for bid, name in ((branch_a, "A"), (branch_b, "B")):
            db.add(Branch(id=bid, name=f"M15 {name} {tag}", city="M15", is_active=True))
            db.add(Batch(id=f"bat-{name.lower()}-{tag}", branch_id=bid, name="Morning"))
        for role, bid, suffix, batch in (
            ("student", branch_a, "a", "Morning"),
            ("student", branch_b, "b", "Morning"),
            ("teacher", branch_a, "ta", None),
            ("teacher", branch_b, "tb", None),
        ):
            u = User(
                id=str(uuid.uuid4()),
                role=role,
                email=f"m15_{suffix}_{tag}@m15.neon",
                username=f"m15_{suffix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=f"M15 {suffix}",
                branch_id=bid,
                batch=batch,
                is_active=True,
            )
            db.add(u)
            ids.append(u.id)
        q = WritingQuestion(
            public_id=f"M15-NEON-{tag}",
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
        qid = q.id
        stu_a, stu_b, tea_a, tea_b = ids
    finally:
        db.close()

    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"m15_a_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"m15_b_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_ta = client.post(
        "/api/auth/login",
        json={"username": f"m15_ta_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]
    tok_tb = client.post(
        "/api/auth/login",
        json={"username": f"m15_tb_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]

    print("=== Assign / visibility ===")
    check(
        client.post(
            "/api/mocks/assignments",
            headers=_hdr(tok_ta),
            json={
                "title": "Foreign",
                "mock_type": "writing",
                "paper_refs": {"question_id": qid},
                "students": [{"id": stu_b, "name": "B"}],
            },
        ).status_code
        == 403,
        "foreign student assign denied",
    )
    created = client.post(
        "/api/mocks/assignments",
        headers=_hdr(tok_ta),
        json={
            "title": f"M15 Writing {tag}",
            "mock_type": "writing",
            "batch_label": "Morning",
            "result_mode": "teacher",
            "paper_refs": {"question_id": qid},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu_a, "name": "A"}],
        },
    )
    check(created.status_code == 200, f"create ({created.status_code})")
    aid = created.json().get("id") if created.status_code == 200 else ""
    if aid:
        assign_ids.append(aid)

    check(
        any(i["id"] == aid for i in client.get("/api/mocks/student/inbox", headers=_hdr(tok_a)).json().get("items") or []),
        "Student A sees mock",
    )
    check(
        all(i["id"] != aid for i in client.get("/api/mocks/student/inbox", headers=_hdr(tok_b)).json().get("items") or []),
        "Student B denied same batch name",
    )

    print("=== Start / linkage / submit / review ===")
    start = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(tok_a),
        json={"assignment_id": aid, "consent": True},
    )
    check(start.status_code == 200, f"start ({start.status_code})")
    attempt_id = start.json().get("attempt_id") if start.status_code == 200 else ""
    skill = start.json().get("skill") or {}
    ref = skill.get("ref")
    resume = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(tok_a),
        json={"assignment_id": aid, "consent": True},
    )
    check(resume.status_code == 200 and resume.json().get("attempt_id") == attempt_id, "resume same attempt")

    db = SessionLocal()
    try:
        wa = db.query(WritingAttempt).filter_by(id=ref).first() if ref else None
        check(wa is not None and wa.student_id == stu_a, "linked writing owned by Student A")
        if wa:
            wa.estimated_band = 6.0
            db.commit()
    finally:
        db.close()

    check(
        client.post(f"/api/mocks/attempts/{attempt_id}/submit", headers=_hdr(tok_b), json={}).status_code == 404,
        "Student B submit denied",
    )
    sub = client.post(f"/api/mocks/attempts/{attempt_id}/submit", headers=_hdr(tok_a), json={})
    check(sub.status_code == 200 and sub.json().get("status") == "submitted", "submit")
    dup = client.post(f"/api/mocks/attempts/{attempt_id}/submit", headers=_hdr(tok_a), json={})
    check(dup.status_code == 200 and dup.json().get("status") == "submitted", "idempotent submit")

    db = SessionLocal()
    try:
        att = db.query(MockAttempt).filter_by(id=attempt_id).first()
        check(att is not None and att.writing_band == 6.0 and att.overall_band == 6.0, "Neon band persistence")
        ev = db.query(MockIntegrityEvent).filter_by(attempt_id=attempt_id, event_type="TEST_SUBMITTED").count()
        check(ev == 1, "single TEST_SUBMITTED event")
    finally:
        db.close()

    check(
        client.post(
            f"/api/mocks/attempts/{attempt_id}/review",
            headers=_hdr(tok_tb),
            json={"writing_band": 9, "publish": True},
        ).status_code
        == 404,
        "Teacher B review denied",
    )
    rev = client.post(
        f"/api/mocks/attempts/{attempt_id}/review",
        headers=_hdr(tok_ta),
        json={"writing_band": 6.5, "publish": True},
    )
    check(rev.status_code == 200 and rev.json().get("overall_band") == 6.5, "teacher override recomputes overall")

    print("=== Speaking ownership ===")
    sp = client.post(
        "/api/mocks/assignments",
        headers=_hdr(tok_ta),
        json={
            "title": f"M15 Speak {tag}",
            "mock_type": "speaking",
            "paper_source": "manual",
            "paper_refs": {"prompt": "Talk about books."},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu_a, "name": "A"}],
        },
    )
    check(sp.status_code == 200, "create speaking mock")
    sp_id = sp.json().get("id") if sp.status_code == 200 else ""
    if sp_id:
        assign_ids.append(sp_id)
    sp_start = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(tok_a),
        json={"assignment_id": sp_id, "consent": True},
    )
    check(sp_start.status_code == 200, f"speaking start ({sp_start.status_code})")
    sess_id = (sp_start.json().get("skill") or {}).get("sessionId")
    db = SessionLocal()
    try:
        from app.speaking.models import TestSession

        sess = db.query(TestSession).filter_by(id=sess_id).first() if sess_id else None
        check(sess is not None and sess.student_id == stu_a, "speaking session owned")
        if sess:
            db.delete(sess)
            db.commit()
    finally:
        db.close()

    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        for aid2 in assign_ids:
            for att in db.query(MockAttempt).filter_by(assignment_id=aid2).all():
                for ev in db.query(MockIntegrityEvent).filter_by(attempt_id=att.id).all():
                    db.delete(ev)
                db.delete(att)
            from app.mocks.models import MockAssignmentStudent

            for slot in db.query(MockAssignmentStudent).filter_by(assignment_id=aid2).all():
                db.delete(slot)
            row = db.query(MockAssignment).filter_by(id=aid2).first()
            if row:
                db.delete(row)
        for wa in db.query(WritingAttempt).filter_by(question_id=qid).all():
            db.delete(wa)
        qrow = db.query(WritingQuestion).filter_by(id=qid).first()
        if qrow:
            db.delete(qrow)
        for uid in ids:
            u = db.query(User).filter_by(id=uid).first()
            if u:
                db.delete(u)
        for bid in (branch_a, branch_b):
            for bat in db.query(Batch).filter_by(branch_id=bid).all():
                db.delete(bat)
            br = db.query(Branch).filter_by(id=bid).first()
            if br:
                db.delete(br)
        db.commit()
        print("  cleaned M15 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM15_NEON_MOCK_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM15_NEON_MOCK_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
