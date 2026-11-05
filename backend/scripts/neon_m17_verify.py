"""Controlled Neon verification for Module 17 — Progress / Analytics."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Batch, Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import SessionLocal
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.main import app
from app.reading.models import ReadingAttempt
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
    branch_a = f"m17-neon-a-{tag}"
    branch_b = f"m17-neon-b-{tag}"
    ids: list[str] = []
    failures: list[str] = []
    qid = ""
    attempt_ids: list[str] = []
    hw_id = ""

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print(f"  FAIL: {msg}")
        else:
            print(f"  OK: {msg}")

    client = TestClient(app)
    db = SessionLocal()
    try:
        print("=== Seed M17 Neon fixtures ===")
        for bid, name in ((branch_a, "A"), (branch_b, "B")):
            db.add(Branch(id=bid, name=f"M17 {name} {tag}", city="M17", is_active=True))
            db.add(Batch(id=f"bat-{name.lower()}-{tag}", branch_id=bid, name="Morning"))
        users = []
        for role, bid, suffix, batch in (
            ("student", branch_a, "a", "Morning"),
            ("student", branch_b, "b", "Morning"),
            ("teacher", branch_a, "ta", None),
            ("teacher", branch_b, "tb", None),
            ("branch_admin", branch_a, "ba", None),
            ("branch_admin", branch_b, "bb", None),
            ("super_admin", None, "sa", None),
        ):
            u = User(
                id=str(uuid.uuid4()),
                role=role,
                email=f"m17_{suffix}_{tag}@m17.neon",
                username=f"m17_{suffix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=f"M17 {suffix}",
                branch_id=bid,
                batch=batch,
                is_active=True,
            )
            db.add(u)
            users.append((role, suffix, u))
            ids.append(u.id)
        q = WritingQuestion(
            public_id=f"M17N-{tag}",
            test_type="academic",
            task_number=2,
            question_type="opinion",
            prompt="Discuss neon analytics.",
            minimum_words=250,
            recommended_minutes=40,
            status="published",
            source_type="original_generated",
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        qid = q.id
        stu_a = next(u for r, s, u in users if s == "a")
        tea_a = next(u for r, s, u in users if s == "ta")
        stu_a_id = stu_a.id
        tea_a_id = tea_a.id
        reading = ReadingAttempt(
            id=str(uuid.uuid4()),
            student_id=stu_a_id,
            test_id="m17-r",
            test_type="academic",
            mode="practice",
            status="submitted",
            estimated_band=7.0,
            raw_score=32,
            started_at=datetime.utcnow(),
            submitted_at=datetime.utcnow(),
        )
        db.add(reading)
        attempt_ids.append(reading.id)
        wa = WritingAttempt(
            id=str(uuid.uuid4()),
            question_id=qid,
            student_id=stu_a_id,
            status="graded",
            estimated_band=6.0,
            teacher_band=6.5,
            final_band=6.5,
            answer_text="x" * 300,
            word_count=300,
            submitted_at=datetime.utcnow(),
            started_at=datetime.utcnow(),
        )
        db.add(wa)
        attempt_ids.append(wa.id)
        asn = HomeworkAssignment(
            id=str(uuid.uuid4()),
            title=f"M17 HW {tag}",
            module="writing",
            scope="piece",
            source="manual",
            created_by=tea_a_id,
        )
        asn.student_ids = [stu_a_id]
        db.add(asn)
        db.commit()
        hw_id = asn.id
        db.add(
            HomeworkSubmission(
                id=str(uuid.uuid4()),
                assignment_id=hw_id,
                student_id=stu_a_id,
                status="submitted",
                submitted_at=datetime.utcnow(),
            )
        )
        db.commit()
    finally:
        db.close()

    tokens = {}
    for role, suffix, _ in users:
        login = client.post(
            "/api/auth/login",
            json={"username": f"m17_{suffix}_{tag}", "password": "Passw0rd!"},
        )
        check(login.status_code == 200, f"login {suffix}")
        if login.status_code == 200:
            tokens[suffix] = login.json()["access_token"]

    print("=== Student A analytics ===")
    stu = client.get("/api/analytics/student", headers=_hdr(tokens["a"]))
    check(stu.status_code == 200, f"student analytics ({stu.status_code})")
    if stu.status_code == 200:
        body = stu.json()
        check(body["skills"]["reading"]["average_band"] == 7.0, "reading avg 7.0")
        check(body["skills"]["writing"]["average_band"] == 6.5, "writing prefers teacher 6.5")
        check(body["skills"]["overall_ielts"] is None, "no fake four-skill overall")

    print("=== Teacher / BA scope ===")
    ta = client.get("/api/analytics/teacher", headers=_hdr(tokens["ta"]))
    check(ta.status_code == 200 and ta.json()["pending_reviews"] >= 1, "teacher A pending reviews")
    check(ta.json().get("branch_id") == branch_a, "teacher A branch scope")
    tb = client.get("/api/analytics/teacher", headers=_hdr(tokens["tb"]))
    check(tb.status_code == 200 and tb.json().get("branch_id") == branch_b, "teacher B branch")
    check(
        client.get(f"/api/analytics/student-record/{stu_a_id}", headers=_hdr(tokens["tb"])).status_code
        in (403, 404),
        "teacher B denied student A record",
    )
    ba = client.get("/api/analytics/branch-admin", headers=_hdr(tokens["ba"]))
    check(ba.status_code == 200 and ba.json()["branch_id"] == branch_a, "BA A campus only")
    check(ba.json()["active_students"] >= 1, "BA A students")
    check(
        client.get("/api/analytics/super-admin", headers=_hdr(tokens["ba"])).status_code == 403,
        "BA denied SA analytics",
    )

    print("=== Super Admin ===")
    sa = client.get("/api/analytics/super-admin", headers=_hdr(tokens["sa"]))
    check(sa.status_code == 200 and sa.json()["branches_total"] >= 2, "SA global branches")
    check(sa.json()["active_students"] >= 2, "SA global students")

    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        for sid in attempt_ids:
            wa = db.query(WritingAttempt).filter_by(id=sid).first()
            if wa:
                db.delete(wa)
            ra = db.query(ReadingAttempt).filter_by(id=sid).first()
            if ra:
                db.delete(ra)
        if hw_id:
            for s in db.query(HomeworkSubmission).filter_by(assignment_id=hw_id).all():
                db.delete(s)
            row = db.query(HomeworkAssignment).filter_by(id=hw_id).first()
            if row:
                db.delete(row)
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
        print("  cleaned M17 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM17_NEON_ANALYTICS_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM17_NEON_ANALYTICS_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
