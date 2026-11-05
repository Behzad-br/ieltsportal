"""Controlled Neon verification for Module 16 — Results."""
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
from app.mocks.models import MockAssignment, MockAttempt
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
    branch_a = f"m16-neon-a-{tag}"
    branch_b = f"m16-neon-b-{tag}"
    ids: list[str] = []
    failures: list[str] = []
    qid = ""
    attempt_id = ""
    mock_id = ""
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
        print("=== Neon results integrity snapshot ===")
        invalid_w = 0
        for a in db.query(WritingAttempt).limit(300).all():
            for band in (a.estimated_band, a.teacher_band, a.final_band):
                if band is not None and (band < 0 or band > 9):
                    invalid_w += 1
        print(f"  writing_attempts_invalid_bands={invalid_w}")

        for bid, name in ((branch_a, "A"), (branch_b, "B")):
            db.add(Branch(id=bid, name=f"M16 {name} {tag}", city="M16", is_active=True))
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
                email=f"m16_{suffix}_{tag}@m16.neon",
                username=f"m16_{suffix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=f"M16 {suffix}",
                branch_id=bid,
                batch=batch,
                is_active=True,
            )
            db.add(u)
            ids.append(u.id)
        q = WritingQuestion(
            public_id=f"M16-NEON-{tag}",
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
        json={"username": f"m16_a_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"m16_b_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_ta = client.post(
        "/api/auth/login",
        json={"username": f"m16_ta_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]
    tok_tb = client.post(
        "/api/auth/login",
        json={"username": f"m16_tb_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]

    print("=== Writing AI -> teacher override ===")
    start = client.post(f"/api/writing/questions/M16-NEON-{tag}/start", headers=_hdr(tok_a))
    check(start.status_code == 200, f"writing start ({start.status_code})")
    attempt_id = start.json().get("id") if start.status_code == 200 else ""
    client.patch(
        f"/api/writing/attempts/{attempt_id}",
        headers=_hdr(tok_a),
        json={"answer_text": "Education matters for society in many important ways. " * 25},
    )
    db = SessionLocal()
    try:
        wa = db.query(WritingAttempt).filter_by(id=attempt_id).first()
        if wa:
            wa.estimated_band = 6.0
            wa.final_band = 6.0
            wa.status = "graded"
            db.commit()
    finally:
        db.close()

    check(
        client.get(f"/api/writing/attempts/{attempt_id}", headers=_hdr(tok_b)).status_code in (403, 404),
        "Student B writing denied",
    )
    own = client.get(f"/api/writing/attempts/{attempt_id}", headers=_hdr(tok_a))
    check(own.status_code == 200 and own.json().get("estimated_band") == 6.0, "Student A sees AI estimate")

    check(
        client.post(
            f"/api/writing/admin/submissions/{attempt_id}/review",
            headers=_hdr(tok_tb),
            json={"teacher_band": 9, "publish": True},
        ).status_code
        in (403, 404),
        "Teacher B review denied",
    )
    check(
        client.post(
            f"/api/writing/admin/submissions/{attempt_id}/review",
            headers=_hdr(tok_ta),
            json={"teacher_band": 12, "publish": True},
        ).status_code
        == 400,
        "invalid band rejected",
    )
    rev = client.post(
        f"/api/writing/admin/submissions/{attempt_id}/review",
        headers=_hdr(tok_ta),
        json={"teacher_band": 6.5, "publish": True},
    )
    check(rev.status_code == 200 and rev.json().get("final_band") == 6.5, "teacher override")
    after = client.get(f"/api/writing/attempts/{attempt_id}", headers=_hdr(tok_a)).json()
    check(
        after.get("estimated_band") == 6.0 and after.get("teacher_band") == 6.5 and after.get("band_source") == "teacher",
        "AI preserved; final=teacher",
    )

    print("=== Mock single-skill result label ===")
    mock = client.post(
        "/api/mocks/assignments",
        headers=_hdr(tok_ta),
        json={
            "title": f"M16 Mock {tag}",
            "mock_type": "writing",
            "result_mode": "ai",
            "paper_refs": {"question_id": qid},
            "available_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z",
            "students": [{"id": stu_a, "name": "A"}],
        },
    )
    check(mock.status_code == 200, f"mock create ({mock.status_code})")
    mock_id = mock.json().get("id") if mock.status_code == 200 else ""
    mstart = client.post(
        "/api/mocks/attempts/start",
        headers=_hdr(tok_a),
        json={"assignment_id": mock_id, "consent": True},
    )
    check(mstart.status_code == 200, f"mock start ({mstart.status_code})")
    mid = mstart.json().get("attempt_id")
    ref = (mstart.json().get("skill") or {}).get("ref")
    db = SessionLocal()
    try:
        wa = db.query(WritingAttempt).filter_by(id=ref).first() if ref else None
        if wa:
            wa.estimated_band = 7.0
            db.commit()
    finally:
        db.close()
    client.post(f"/api/mocks/attempts/{mid}/submit", headers=_hdr(tok_a), json={})
    detail = client.get(f"/api/mocks/student/{mock_id}", headers=_hdr(tok_a))
    check(
        detail.status_code == 200
        and detail.json().get("result_label") == "Writing band"
        and detail.json().get("skill_band") is not None,
        "mock single-skill label",
    )
    check(
        client.get(f"/api/mocks/student/{mock_id}", headers=_hdr(tok_b)).status_code == 404,
        "Student B mock denied",
    )

    print("=== Homework linked result ===")
    hw = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(tok_ta),
        json={
            "title": f"M16 HW {tag}",
            "module": "writing",
            "source": "manual",
            "payload": {"prompt": "Discuss books."},
            "student_ids": [stu_a],
        },
    )
    check(hw.status_code == 200, f"hw create ({hw.status_code})")
    hw_id = hw.json().get("id") if hw.status_code == 200 else ""
    with patch(
        "app.homework.controllers._open_skill",
        new=AsyncMock(return_value={"kind": "writing-question", "questionId": qid, "ref": attempt_id}),
    ):
        hstart = client.post(f"/api/homework/student/{hw_id}/start", headers=_hdr(tok_a))
    check(hstart.status_code == 200, f"hw start ({hstart.status_code})")
    sub_id = hstart.json().get("submission_id")
    client.post(f"/api/homework/student/submissions/{sub_id}/complete", headers=_hdr(tok_a), json={})
    draft = client.post(
        f"/api/homework/teacher/submissions/{sub_id}/review",
        headers=_hdr(tok_ta),
        json={"teacher_band": 6.5, "teacher_comments": "Draft", "publish": False},
    )
    check(draft.status_code == 200 and draft.json().get("status") == "reviewed", "hw teacher draft review")
    pre = client.get(f"/api/homework/student/{hw_id}", headers=_hdr(tok_a))
    check(
        pre.status_code == 200 and pre.json().get("teacher_band") is None and pre.json().get("result_pending") is True,
        "hw teacher band hidden until publish",
    )
    client.post(
        f"/api/homework/teacher/submissions/{sub_id}/review",
        headers=_hdr(tok_ta),
        json={"teacher_band": 6.5, "teacher_comments": "Final", "publish": True},
    )
    hdetail = client.get(f"/api/homework/student/{hw_id}", headers=_hdr(tok_a))
    check(
        hdetail.status_code == 200 and hdetail.json().get("teacher_band") == 6.5,
        "Student A homework published result",
    )
    check(
        client.get(f"/api/homework/student/{hw_id}", headers=_hdr(tok_b)).status_code in (403, 404),
        "Student B homework denied",
    )

    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        if mock_id:
            for att in db.query(MockAttempt).filter_by(assignment_id=mock_id).all():
                db.delete(att)
            from app.mocks.models import MockAssignmentStudent

            for slot in db.query(MockAssignmentStudent).filter_by(assignment_id=mock_id).all():
                db.delete(slot)
            row = db.query(MockAssignment).filter_by(id=mock_id).first()
            if row:
                db.delete(row)
        if hw_id:
            for s in db.query(HomeworkSubmission).filter_by(assignment_id=hw_id).all():
                db.delete(s)
            row = db.query(HomeworkAssignment).filter_by(id=hw_id).first()
            if row:
                db.delete(row)
        for wa in db.query(WritingAttempt).filter(WritingAttempt.question_id == qid).all():
            db.delete(wa)
        if attempt_id:
            wa = db.query(WritingAttempt).filter_by(id=attempt_id).first()
            if wa:
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
        print("  cleaned M16 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM16_NEON_RESULTS_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM16_NEON_RESULTS_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
