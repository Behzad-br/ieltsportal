"""Controlled Neon verification for Module 13 — Homework."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Batch, Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import SessionLocal
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.main import app
from app.speaking.models import TestSession


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch_a = f"m13-neon-a-{tag}"
    branch_b = f"m13-neon-b-{tag}"
    ids: list[str] = []
    hw_ids: list[str] = []
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
        print("=== Neon homework integrity snapshot ===")
        assigns = db.query(HomeworkAssignment).count()
        subs = db.query(HomeworkSubmission).count()
        print(f"  assignments={assigns} submissions={subs}")

        for bid, name in ((branch_a, "A"), (branch_b, "B")):
            db.add(Branch(id=bid, name=f"M13 {name} {tag}", city="M13", is_active=True))
            db.add(Batch(id=f"bat-{name.lower()}-{tag}", branch_id=bid, name="Morning M13"))
        users_spec = (
            ("student", branch_a, "a", "Morning M13"),
            ("student", branch_b, "b", "Morning M13"),
            ("teacher", branch_a, "ta", None),
            ("teacher", branch_b, "tb", None),
        )
        for role, bid, suffix, batch in users_spec:
            u = User(
                id=str(uuid.uuid4()),
                role=role,
                email=f"m13_{suffix}_{tag}@m13.neon",
                username=f"m13_{suffix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=f"M13 {suffix}",
                branch_id=bid,
                batch=batch,
                is_active=True,
            )
            db.add(u)
            ids.append(u.id)
        db.commit()
        stu_a, stu_b, tea_a, tea_b = ids
    finally:
        db.close()

    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"m13_a_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"m13_b_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_ta = client.post(
        "/api/auth/login",
        json={"username": f"m13_ta_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]
    tok_tb = client.post(
        "/api/auth/login",
        json={"username": f"m13_tb_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]

    print("=== Create / visibility ===")
    direct = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(tok_ta),
        json={
            "title": "Direct M13",
            "module": "writing",
            "source": "manual",
            "payload": {"prompt": "Discuss education."},
            "student_ids": [stu_a],
            "deadline": (datetime.utcnow() + timedelta(days=3)).isoformat() + "Z",
        },
    )
    check(direct.status_code == 200, f"create direct ({direct.status_code})")
    direct_id = direct.json().get("id") if direct.status_code == 200 else ""
    if direct_id:
        hw_ids.append(direct_id)

    batch = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(tok_ta),
        json={
            "title": "Batch M13",
            "module": "writing",
            "source": "manual",
            "payload": {"prompt": "Batch essay."},
            "student_ids": [],
            "batch_label": "Morning M13",
            "assignment_target": "entire",
        },
    )
    check(batch.status_code == 200, f"create batch ({batch.status_code})")
    batch_id = batch.json().get("id") if batch.status_code == 200 else ""
    if batch_id:
        hw_ids.append(batch_id)

    check(
        client.post(
            "/api/homework/teacher/assignments",
            headers=_hdr(tok_ta),
            json={
                "title": "Foreign",
                "module": "writing",
                "payload": {"prompt": "x"},
                "student_ids": [stu_b],
            },
        ).status_code
        == 403,
        "foreign student assign denied",
    )

    list_a = client.get("/api/homework/student", headers=_hdr(tok_a))
    ids_a = {x["id"] for x in list_a.json().get("assignments") or []}
    check(direct_id in ids_a and batch_id in ids_a, "Student A sees direct+batch")
    list_b = client.get("/api/homework/student", headers=_hdr(tok_b))
    ids_b = {x["id"] for x in list_b.json().get("assignments") or []}
    check(direct_id not in ids_b and batch_id not in ids_b, "Student B denied cross-campus batch name")

    print("=== Submit / review / IDOR ===")
    with patch(
        "app.homework.controllers._open_skill",
        new=AsyncMock(return_value={"kind": "writing-question", "questionId": "q", "ref": "ref1"}),
    ):
        start = client.post(f"/api/homework/student/{direct_id}/start", headers=_hdr(tok_a))
    check(start.status_code == 200, f"start ({start.status_code})")
    sub_id = start.json().get("submission_id") if start.status_code == 200 else ""
    done = client.post(
        f"/api/homework/student/submissions/{sub_id}/complete",
        headers=_hdr(tok_a),
        json={},
    )
    check(done.status_code == 200 and done.json().get("status") == "submitted", "complete")
    dup = client.post(
        f"/api/homework/student/submissions/{sub_id}/complete",
        headers=_hdr(tok_a),
        json={"estimated_band": 9},
    )
    check(dup.status_code == 200 and dup.json().get("status") == "submitted", "duplicate complete safe")
    check(client.post(f"/api/homework/student/{direct_id}/start", headers=_hdr(tok_a)).status_code == 409, "no reopen")

    review = client.post(
        f"/api/homework/teacher/submissions/{sub_id}/review",
        headers=_hdr(tok_ta),
        json={"teacher_band": 6.5, "teacher_comments": "Good", "publish": True},
    )
    check(review.status_code == 200 and review.json().get("teacher_band") == 6.5, "teacher A review")
    check(
        client.post(
            f"/api/homework/teacher/submissions/{sub_id}/review",
            headers=_hdr(tok_tb),
            json={"teacher_band": 9},
        ).status_code
        == 403,
        "teacher B denied",
    )

    db = SessionLocal()
    try:
        sub = db.query(HomeworkSubmission).filter_by(id=sub_id).first()
        check(
            sub is not None and sub.status == "published" and sub.teacher_band == 6.5 and sub.student_id == stu_a,
            "Neon submission persistence",
        )
    finally:
        db.close()

    print("=== Speaking ownership regression ===")
    sp = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(tok_ta),
        json={
            "title": "Speak M13",
            "module": "speaking",
            "source": "manual",
            "task": "Part 1",
            "student_ids": [stu_a],
            "payload": {"prompt": "Do you like music?"},
        },
    )
    check(sp.status_code == 200, "create speaking hw")
    sp_id = sp.json().get("id") if sp.status_code == 200 else ""
    if sp_id:
        hw_ids.append(sp_id)
    start_sp = client.post(f"/api/homework/student/{sp_id}/start", headers=_hdr(tok_a))
    check(start_sp.status_code == 200, f"speaking start ({start_sp.status_code})")
    sess_id = start_sp.json().get("sessionId") if start_sp.status_code == 200 else ""
    db = SessionLocal()
    try:
        sess = db.query(TestSession).filter_by(id=sess_id).first() if sess_id else None
        check(sess is not None and sess.student_id == stu_a, "speaking session owned by Student A")
        if sess:
            db.delete(sess)
            db.commit()
    finally:
        db.close()

    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        for hid in hw_ids:
            for s in db.query(HomeworkSubmission).filter_by(assignment_id=hid).all():
                db.delete(s)
            row = db.query(HomeworkAssignment).filter_by(id=hid).first()
            if row:
                db.delete(row)
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
        print("  cleaned M13 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM13_NEON_HOMEWORK_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM13_NEON_HOMEWORK_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
