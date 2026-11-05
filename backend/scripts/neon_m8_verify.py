"""Controlled Neon verification for Module 8 — Batches / Classes."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Batch, Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import SessionLocal
from app.homework.models import HomeworkAssignment
from app.main import app
from app.notes.models import ClassNote


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch_a = f"m8-neon-a-{tag}"
    branch_b = f"m8-neon-b-{tag}"
    ids: list[str] = []
    batch_ids: list[str] = []
    hw_id = f"m8-hw-{tag}"
    note_id = f"m8-note-{tag}"
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
        for bid, name in ((branch_a, f"M8-A {tag}"), (branch_b, f"M8-B {tag}")):
            db.add(Branch(id=bid, name=name, city=name, is_active=True))
        sa = User(
            id=str(uuid.uuid4()),
            role="super_admin",
            email=f"sa_{tag}@m8.neon",
            username=f"sa_m8_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="SA",
            is_active=True,
        )
        ba_a = User(
            id=str(uuid.uuid4()),
            role="branch_admin",
            email=f"ba_a_{tag}@m8.neon",
            username=f"ba_a_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="BA A",
            branch_id=branch_a,
            is_active=True,
        )
        ba_b = User(
            id=str(uuid.uuid4()),
            role="branch_admin",
            email=f"ba_b_{tag}@m8.neon",
            username=f"ba_b_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="BA B",
            branch_id=branch_b,
            is_active=True,
        )
        t_a = User(
            id=str(uuid.uuid4()),
            role="teacher",
            email=f"t_a_{tag}@m8.neon",
            username=f"t_a_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="T A",
            branch_id=branch_a,
            is_active=True,
        )
        t_b = User(
            id=str(uuid.uuid4()),
            role="teacher",
            email=f"t_b_{tag}@m8.neon",
            username=f"t_b_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="T B",
            branch_id=branch_b,
            is_active=True,
        )
        for u in (sa, ba_a, ba_b, t_a, t_b):
            db.add(u)
            ids.append(u.id)
        db.commit()
        teacher_a_id = t_a.id
        teacher_b_id = t_b.id
        ba_a_user = ba_a.username
        ba_b_user = ba_b.username
        sa_user = sa.username
    finally:
        db.close()

    tok_sa = client.post(
        "/api/auth/login", json={"username": sa_user, "password": "Passw0rd!", "role": "super_admin"}
    ).json()["access_token"]
    tok_ba_a = client.post(
        "/api/auth/login", json={"username": ba_a_user, "password": "Passw0rd!", "role": "branch_admin"}
    ).json()["access_token"]
    tok_ba_b = client.post(
        "/api/auth/login", json={"username": ba_b_user, "password": "Passw0rd!", "role": "branch_admin"}
    ).json()["access_token"]

    print("=== Create / list ===")
    a1 = client.post(
        "/api/org/batches",
        headers=_hdr(tok_ba_a),
        json={"name": f"Batch A1 {tag}", "branch_id": branch_a, "teacher_id": teacher_a_id},
    )
    check(a1.status_code == 200, f"create A1 ({a1.status_code})")
    a1j = a1.json() if a1.status_code == 200 else {}
    if a1j.get("id"):
        batch_ids.append(a1j["id"])

    a2 = client.post(
        "/api/org/batches",
        headers=_hdr(tok_ba_a),
        json={"name": f"Batch A2 {tag}", "branch_id": branch_a},
    )
    check(a2.status_code == 200, f"create A2 ({a2.status_code})")
    if a2.status_code == 200:
        batch_ids.append(a2.json()["id"])

    b1 = client.post(
        "/api/org/batches",
        headers=_hdr(tok_ba_b),
        json={"name": f"Batch B1 {tag}", "branch_id": branch_b, "teacher_id": teacher_b_id},
    )
    check(b1.status_code == 200, f"create B1 ({b1.status_code})")
    if b1.status_code == 200:
        batch_ids.append(b1.json()["id"])

    check(
        client.post(
            "/api/org/batches",
            headers=_hdr(tok_ba_a),
            json={"name": f"Hack {tag}", "branch_id": branch_b},
        ).status_code
        == 403,
        "BA A foreign create denied",
    )
    check(
        client.post(
            "/api/org/batches",
            headers=_hdr(tok_ba_a),
            json={"name": f"  Batch A1 {tag}  ", "branch_id": branch_a},
        ).status_code
        == 409,
        "whitespace duplicate returns 409",
    )

    ba_list = client.get("/api/org/batches", headers=_hdr(tok_ba_a))
    check(ba_list.status_code == 200 and all(x["branchId"] == branch_a for x in ba_list.json()["batches"]), "BA list scoped")
    check("unassigned_students" in ba_list.json(), "unassigned_students in campus list")

    print("=== Teacher reassignment sync ===")
    t2 = User(
        id=str(uuid.uuid4()),
        role="teacher",
        email=f"t_a2_{tag}@m8.neon",
        username=f"t_a2_{tag}",
        password_hash=hash_password("Passw0rd!"),
        full_name="T A2",
        branch_id=branch_a,
        is_active=True,
    )
    db = SessionLocal()
    try:
        db.add(t2)
        db.commit()
        ids.append(t2.id)
        t2_id = t2.id
    finally:
        db.close()

    re = client.patch(
        f"/api/org/batches/{a1j['id']}",
        headers=_hdr(tok_ba_a),
        json={"teacher_id": t2_id},
    )
    check(re.status_code == 200, f"reassign teacher ({re.status_code})")
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter_by(id=a1j["id"]).first()
        t1 = db.query(User).filter_by(id=teacher_a_id).first()
        t2row = db.query(User).filter_by(id=t2_id).first()
        check(batch is not None and batch.teacher_id == t2_id, "Batch.teacher_id updated")
        check(f"Batch A1 {tag}" not in (t1.batches or ""), "old teacher CSV cleared")
        check(f"Batch A1 {tag}" in (t2row.batches or ""), "new teacher CSV set")
    finally:
        db.close()

    print("=== Student assign / move / foreign deny ===")
    stu = client.post(
        "/api/org/users",
        headers=_hdr(tok_ba_a),
        json={
            "role": "student",
            "email": f"stu_a_{tag}@m8.neon",
            "username": f"stu_a_{tag}",
            "password": "Student1!",
            "full_name": "Student A",
            "branch_id": branch_a,
            "batch": f"Batch A1 {tag}",
        },
    )
    check(stu.status_code == 200, f"create student ({stu.status_code})")
    stu_id = stu.json()["id"] if stu.status_code == 200 else ""
    if stu_id:
        ids.append(stu_id)

    move = client.patch(
        f"/api/org/users/{stu_id}",
        headers=_hdr(tok_ba_a),
        json={"batch": f"Batch A2 {tag}"},
    )
    check(move.status_code == 200, "move student A1 to A2")
    check(
        client.patch(
            f"/api/org/users/{stu_id}",
            headers=_hdr(tok_ba_a),
            json={"batch": f"Batch B1 {tag}"},
        ).status_code
        == 400,
        "foreign batch assign denied",
    )
    rem = client.patch(
        f"/api/org/users/{stu_id}",
        headers=_hdr(tok_ba_a),
        json={"batch": ""},
    )
    check(rem.status_code == 200, "remove student from batch")
    db = SessionLocal()
    try:
        row = db.query(User).filter_by(id=stu_id).first()
        check(row is not None and (row.batch or "") == "" and row.branch_id == branch_a, "unassigned keeps branch")
    finally:
        db.close()

    print("=== Rename cascade ===")
    # put student back on A1 for rename
    client.patch(f"/api/org/users/{stu_id}", headers=_hdr(tok_ba_a), json={"batch": f"Batch A1 {tag}"})
    db = SessionLocal()
    try:
        hw = HomeworkAssignment(
            id=hw_id,
            title="M8 HW",
            module="writing",
            scope="piece",
            source="bank",
            batch_label=f"Batch A1 {tag}",
            created_by=t2_id,
        )
        hw.payload = {}
        db.add(hw)
        note = ClassNote(
            id=note_id,
            title="M8 N",
            description="",
            original_name="n.pdf",
            stored_name="n.pdf",
            page_count=1,
            created_by=t2_id,
        )
        note.unlocked_batches = [f"Batch A1 {tag}"]
        note.unlocked_student_ids = []
        db.add(note)
        db.commit()
    finally:
        db.close()

    ren = client.patch(
        f"/api/org/batches/{a1j['id']}",
        headers=_hdr(tok_ba_a),
        json={"name": f"Batch A1 Renamed {tag}"},
    )
    check(ren.status_code == 200, f"rename ({ren.status_code})")
    db = SessionLocal()
    try:
        stu_row = db.query(User).filter_by(id=stu_id).first()
        hw = db.query(HomeworkAssignment).filter_by(id=hw_id).first()
        note = db.query(ClassNote).filter_by(id=note_id).first()
        t2row = db.query(User).filter_by(id=t2_id).first()
        check(stu_row is not None and stu_row.batch == f"Batch A1 Renamed {tag}", "student label cascaded")
        check(hw is not None and hw.batch_label == f"Batch A1 Renamed {tag}", "homework label cascaded")
        check(note is not None and note.unlocked_batches == [f"Batch A1 Renamed {tag}"], "notes label cascaded")
        check(f"Batch A1 Renamed {tag}" in (t2row.batches or ""), "teacher CSV cascaded")
    finally:
        db.close()

    print("=== Cross-branch IDOR ===")
    check(
        client.patch(
            f"/api/org/batches/{b1.json()['id']}",
            headers=_hdr(tok_ba_a),
            json={"schedule": "nope"},
        ).status_code
        == 403,
        "BA A cannot edit B1",
    )

    print("=== Cleanup ===")
    # unassign student then delete batches
    client.patch(f"/api/org/users/{stu_id}", headers=_hdr(tok_ba_a), json={"batch": ""})
    db = SessionLocal()
    try:
        for mid, model in ((hw_id, HomeworkAssignment), (note_id, ClassNote)):
            row = db.query(model).filter_by(id=mid).first()
            if row:
                db.delete(row)
        for bid in batch_ids:
            b = db.query(Batch).filter_by(id=bid).first()
            if b:
                db.delete(b)
        for uid in ids:
            u = db.query(User).filter_by(id=uid).first()
            if u:
                db.delete(u)
        for bid in (branch_a, branch_b):
            br = db.query(Branch).filter_by(id=bid).first()
            if br:
                db.delete(br)
        db.commit()
        print("  cleaned M8 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM8_NEON_BATCH_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM8_NEON_BATCH_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
