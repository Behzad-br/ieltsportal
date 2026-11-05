"""
Controlled Neon verification for Module 7 — Student Management.
Creates Branch M7-A/B + BA/Teacher/Student/Batch, exercises critical flows, cleans up.
Prints M7_NEON_STUDENT_PASS only on full success.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Ensure backend root is on path when run as script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Batch, Branch, User
from app.auth.security import hash_password, verify_password
from app.config import settings
from app.database import SessionLocal
from app.homework.models import HomeworkAssignment
from app.listening.models import ListeningAttempt
from app.main import app
from app.notes.models import ClassNote
from app.reading.models import ReadingAttempt
from app.writing.models import WritingAttempt, WritingQuestion


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    # JWT students must ignore header spoofing
    settings.AUTH_LEGACY_HEADERS = False

    tag = uuid.uuid4().hex[:8]
    branch_a = f"m7-neon-a-{tag}"
    branch_b = f"m7-neon-b-{tag}"
    batch_a = f"Morning M7 {tag}"
    batch_b = f"Evening M7 {tag}"
    ids: list[str] = []
    batch_ids: list[str] = []
    note_id = f"m7-note-{tag}"
    hw_id = f"m7-hw-{tag}"
    ra_id = f"m7-ra-{tag}"
    la_id = f"m7-la-{tag}"
    wa_id = f"m7-wa-{tag}"
    wq_id = f"m7-wq-{tag}"

    client = TestClient(app)
    failures: list[str] = []
    teacher_a_id = ""

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print(f"  FAIL: {msg}")
        else:
            print(f"  OK: {msg}")

    db = SessionLocal()
    try:
        print("=== Neon integrity snapshot (pre) ===")
        students = db.query(User).filter_by(role="student").all()
        branch_ids = {b.id for b in db.query(Branch).all()}
        orphan_branch = sum(1 for s in students if s.branch_id and s.branch_id not in branch_ids)
        inactive_branch_students = 0
        for s in students:
            if not s.branch_id:
                continue
            br = db.query(Branch).filter_by(id=s.branch_id).first()
            if br is not None and not br.is_active:
                inactive_branch_students += 1
        cross_batch = 0
        for s in students:
            label = (s.batch or "").strip()
            if not label or not s.branch_id:
                continue
            if not db.query(Batch).filter_by(branch_id=s.branch_id, name=label).first():
                cross_batch += 1
        print(f"  students={len(students)} orphan_branch={orphan_branch} "
              f"inactive_branch_students={inactive_branch_students} "
              f"batch_label_mismatch={cross_batch}")

        # Seed campuses + staff
        for bid, name in ((branch_a, f"M7-A {tag}"), (branch_b, f"M7-B {tag}")):
            db.add(Branch(id=bid, name=name, city=name, is_active=True))
        ba_a = User(
            id=str(uuid.uuid4()),
            role="branch_admin",
            email=f"ba_a_{tag}@m7.neon",
            username=f"ba_a_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="BA A",
            branch_id=branch_a,
            is_active=True,
        )
        ba_b = User(
            id=str(uuid.uuid4()),
            role="branch_admin",
            email=f"ba_b_{tag}@m7.neon",
            username=f"ba_b_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="BA B",
            branch_id=branch_b,
            is_active=True,
        )
        t_a = User(
            id=str(uuid.uuid4()),
            role="teacher",
            email=f"t_a_{tag}@m7.neon",
            username=f"t_a_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="Teacher A",
            branch_id=branch_a,
            is_active=True,
        )
        t_b = User(
            id=str(uuid.uuid4()),
            role="teacher",
            email=f"t_b_{tag}@m7.neon",
            username=f"t_b_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="Teacher B",
            branch_id=branch_b,
            is_active=True,
        )
        sa = User(
            id=str(uuid.uuid4()),
            role="super_admin",
            email=f"sa_{tag}@m7.neon",
            username=f"sa_m7_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="SA M7",
            branch_id="",
            is_active=True,
        )
        for u in (ba_a, ba_b, t_a, t_b, sa):
            db.add(u)
            ids.append(u.id)
        b_row_a = Batch(id=str(uuid.uuid4()), name=batch_a, branch_id=branch_a)
        b_row_b = Batch(id=str(uuid.uuid4()), name=batch_b, branch_id=branch_b)
        db.add(b_row_a)
        db.add(b_row_b)
        batch_ids.extend([b_row_a.id, b_row_b.id])
        db.commit()
        teacher_a_id = t_a.id
    finally:
        db.close()

    print("=== Create / login / batch ===")
    # BA A creates Student A
    r = client.post(
        "/api/org/users",
        headers=_hdr(
            client.post(
                "/api/auth/login",
                json={"username": f"ba_a_{tag}", "password": "Passw0rd!", "role": "branch_admin"},
            ).json()["access_token"]
        ),
        json={
            "role": "student",
            "email": f"stu_a_{tag}@m7.neon",
            "username": f"stu_a_{tag}",
            "password": "Student1!",
            "full_name": "Student A",
            "branch_id": branch_a,
            "batch": batch_a,
        },
    )
    check(r.status_code == 200, f"BA A create student A ({r.status_code})")
    stu_a = r.json() if r.status_code == 200 else {}
    if stu_a.get("id"):
        ids.append(stu_a["id"])

    # BA A cannot create on Branch B
    tok_ba_a = client.post(
        "/api/auth/login",
        json={"username": f"ba_a_{tag}", "password": "Passw0rd!", "role": "branch_admin"},
    ).json()["access_token"]
    r = client.post(
        "/api/org/users",
        headers=_hdr(tok_ba_a),
        json={
            "role": "student",
            "email": f"fx_{tag}@m7.neon",
            "username": f"fx_{tag}",
            "password": "Student1!",
            "full_name": "FX",
            "branch_id": branch_b,
            "batch": batch_b,
        },
    )
    check(r.status_code == 403, f"BA A foreign branch denied ({r.status_code})")

    # SA creates Student B on Branch B
    tok_sa = client.post(
        "/api/auth/login",
        json={"username": f"sa_m7_{tag}", "password": "Passw0rd!", "role": "super_admin"},
    ).json()["access_token"]
    r = client.post(
        "/api/org/users",
        headers=_hdr(tok_sa),
        json={
            "role": "student",
            "email": f"stu_b_{tag}@m7.neon",
            "username": f"stu_b_{tag}",
            "password": "Student1!",
            "full_name": "Student B",
            "branch_id": branch_b,
            "batch": batch_b,
        },
    )
    check(r.status_code == 200, f"SA create student B ({r.status_code})")
    stu_b = r.json() if r.status_code == 200 else {}
    if stu_b.get("id"):
        ids.append(stu_b["id"])

    # Persist check
    db = SessionLocal()
    try:
        row = db.query(User).filter_by(id=stu_a.get("id")).first()
        check(row is not None and row.batch == batch_a and row.branch_id == branch_a, "Neon row student A batch/branch")
        check(verify_password("Student1!", row.password_hash) if row else False, "password hashed")
    finally:
        db.close()

    login_a = client.post(
        "/api/auth/login",
        json={"username": f"stu_a_{tag}", "password": "Student1!", "role": "student"},
    )
    check(login_a.status_code == 200, "student A login")
    tok_a = login_a.json()["access_token"] if login_a.status_code == 200 else ""
    login_b = client.post(
        "/api/auth/login",
        json={"username": f"stu_b_{tag}", "password": "Student1!", "role": "student"},
    )
    check(login_b.status_code == 200, "student B login")
    tok_b = login_b.json()["access_token"] if login_b.status_code == 200 else ""

    print("=== Profile / IDOR / mass assignment ===")
    check(client.get(f"/api/org/users/{stu_a['id']}", headers=_hdr(tok_a)).status_code == 200, "own profile")
    check(client.get(f"/api/org/users/{stu_b['id']}", headers=_hdr(tok_a)).status_code == 403, "cross-student profile denied")
    spoof = client.patch(
        f"/api/org/users/{stu_a['id']}",
        headers=_hdr(tok_a),
        json={"branch_id": branch_b, "batch": batch_b, "is_active": False},
    )
    check(spoof.status_code == 403, "mass assignment denied")

    # Foreign batch assign by BA A
    r = client.patch(
        f"/api/org/users/{stu_a['id']}",
        headers=_hdr(tok_ba_a),
        json={"batch": batch_b},
    )
    check(r.status_code == 400, f"foreign batch rejected ({r.status_code})")

    print("=== Homework / notes / skill ownership ===")
    db = SessionLocal()
    try:
        hw = HomeworkAssignment(
            id=hw_id,
            title="M7 HW",
            module="writing",
            scope="piece",
            source="bank",
            created_by=teacher_a_id,
        )
        hw.student_ids = [stu_a["id"]]
        hw.payload = {"prompt": "x"}
        db.add(hw)
        note = ClassNote(
            id=note_id,
            title="M7 Notes",
            description="",
            original_name="n.pdf",
            stored_name="n.pdf",
            page_count=1,
            created_by=teacher_a_id,
        )
        note.unlocked_batches = [batch_a]
        note.unlocked_student_ids = []
        db.add(note)
        db.add(
            ReadingAttempt(
                id=ra_id,
                student_id=stu_a["id"],
                test_id="t1",
                test_type="academic",
                mode="full_mock",
                status="submitted",
            )
        )
        db.add(
            ListeningAttempt(
                id=la_id,
                student_id=stu_a["id"],
                test_id="t1",
                mode="full_mock",
                status="submitted",
            )
        )
        db.add(
            WritingQuestion(
                id=wq_id,
                public_id=f"pub-{tag}",
                prompt="Write",
                test_type="academic",
                task_number=2,
                question_type="essay",
                status="published",
            )
        )
        db.add(
            WritingAttempt(
                id=wa_id,
                student_id=stu_a["id"],
                question_id=wq_id,
                status="submitted",
                answer_text="hello",
                word_count=1,
            )
        )
        db.commit()
    finally:
        db.close()

    hw_a = client.get("/api/homework/student", headers=_hdr(tok_a))
    check(hw_a.status_code == 200 and any(x["id"] == hw_id for x in hw_a.json().get("assignments", [])), "A sees homework")
    hw_b = client.get("/api/homework/student", headers=_hdr(tok_b))
    check(hw_b.status_code == 200 and all(x.get("id") != hw_id for x in hw_b.json().get("assignments", [])), "B no homework leak")

    notes_a = client.get("/api/notes/student", headers=_hdr(tok_a))
    notes_b = client.get("/api/notes/student", headers=_hdr(tok_b))
    row_a = next((n for n in notes_a.json().get("notes", []) if n["id"] == note_id), None)
    row_b = next((n for n in notes_b.json().get("notes", []) if n["id"] == note_id), None)
    check(bool(row_a and row_a.get("unlocked")), "A notes unlocked via DB batch")
    check(bool(row_b) and not row_b.get("unlocked"), "B notes locked")
    check(client.get(f"/api/notes/student/{note_id}", headers=_hdr(tok_b)).status_code == 403, "B notes detail denied")

    check(client.get(f"/api/reading/attempts/{ra_id}", headers=_hdr(tok_a)).status_code == 200, "A reading own")
    check(client.get(f"/api/reading/attempts/{ra_id}", headers=_hdr(tok_b)).status_code == 403, "B reading IDOR denied")
    check(client.get(f"/api/listening/attempts/{la_id}", headers=_hdr(tok_b)).status_code == 403, "B listening IDOR denied")
    check(client.get(f"/api/writing/attempts/{wa_id}", headers=_hdr(tok_b)).status_code == 403, "B writing IDOR denied")
    spoof_w = client.get(
        f"/api/writing/attempts/{wa_id}",
        headers={**_hdr(tok_b), "X-Student-Id": stu_a["id"]},
    )
    check(spoof_w.status_code == 403, "header spoof writing denied")

    print("=== Deactivate / JWT / reactivate / history ===")
    client.patch(f"/api/org/users/{stu_a['id']}", headers=_hdr(tok_sa), json={"is_active": False})
    check(client.get("/api/auth/me", headers=_hdr(tok_a)).status_code == 403, "old JWT denied after deactivate")
    check(
        client.post(
            "/api/auth/login",
            json={"username": f"stu_a_{tag}", "password": "Student1!", "role": "student"},
        ).status_code
        == 403,
        "login denied while inactive",
    )
    db = SessionLocal()
    try:
        ra = db.query(ReadingAttempt).filter_by(id=ra_id).first()
        check(ra is not None and ra.student_id == stu_a["id"], "history retained after deactivate")
    finally:
        db.close()
    client.patch(f"/api/org/users/{stu_a['id']}", headers=_hdr(tok_sa), json={"is_active": True})
    check(
        client.post(
            "/api/auth/login",
            json={"username": f"stu_a_{tag}", "password": "Student1!", "role": "student"},
        ).status_code
        == 200,
        "reactivate login works",
    )

    # Cleanup
    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        for model, mid in (
            (ReadingAttempt, ra_id),
            (ListeningAttempt, la_id),
            (WritingAttempt, wa_id),
            (WritingQuestion, wq_id),
            (HomeworkAssignment, hw_id),
            (ClassNote, note_id),
        ):
            row = db.query(model).filter_by(id=mid).first()
            if row:
                db.delete(row)
        for uid in ids:
            u = db.query(User).filter_by(id=uid).first()
            if u:
                db.delete(u)
        for bid in batch_ids:
            b = db.query(Batch).filter_by(id=bid).first()
            if b:
                db.delete(b)
        for bid in (branch_a, branch_b):
            br = db.query(Branch).filter_by(id=bid).first()
            if br:
                db.delete(br)
        db.commit()
        print("  cleaned M7 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM7_NEON_STUDENT_FAIL ({len(failures)} failures)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM7_NEON_STUDENT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
