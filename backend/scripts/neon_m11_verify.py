"""Controlled Neon verification for Module 11 — Writing (mocked AI provider)."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.writing.models import WritingAttempt, WritingQuestion


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _grading(band: float = 6.0) -> dict:
    return {
        "estimated_overall_band": band,
        "criteria": {
            "task_response_or_achievement": {"band": band, "feedback": "ok"},
            "coherence_and_cohesion": {"band": band, "feedback": "ok"},
            "lexical_resource": {"band": band, "feedback": "ok"},
            "grammatical_range_and_accuracy": {"band": band, "feedback": "ok"},
        },
        "strengths": ["clear"],
        "priority_improvements": ["examples"],
        "why_this_band": ["balanced"],
        "next_steps": ["practice"],
    }


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    settings.WRITING_AI_GRADING_ENABLED = True
    tag = uuid.uuid4().hex[:8]
    branch_a = f"m11-neon-a-{tag}"
    branch_b = f"m11-neon-b-{tag}"
    ids: list[str] = []
    qid = None
    attempt_ids: list[str] = []
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
        print("=== Neon writing integrity snapshot ===")
        attempts = db.query(WritingAttempt).count()
        orphans = (
            db.query(WritingAttempt)
            .filter(~WritingAttempt.student_id.in_(db.query(User.id)))
            .count()
            if attempts
            else 0
        )
        empty_submitted = (
            db.query(WritingAttempt)
            .filter(
                WritingAttempt.status.in_(("submitted", "grading", "graded", "published", "teacher_review")),
                (WritingAttempt.submitted_text.is_(None)) | (WritingAttempt.submitted_text == ""),
                (WritingAttempt.answer_text.is_(None)) | (WritingAttempt.answer_text == ""),
            )
            .count()
        )
        print(f"  attempts={attempts} orphan_student_refs={orphans} empty_submitted={empty_submitted}")

        for bid, name in ((branch_a, "A"), (branch_b, "B")):
            db.add(Branch(id=bid, name=f"M11 {name} {tag}", city="M11", is_active=True))
        users = []
        for role, bid, suffix in (
            ("student", branch_a, "a"),
            ("student", branch_b, "b"),
            ("teacher", branch_a, "ta"),
            ("teacher", branch_b, "tb"),
        ):
            u = User(
                id=str(uuid.uuid4()),
                role=role,
                email=f"m11_{suffix}_{tag}@m11.neon",
                username=f"m11_{suffix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=f"M11 {suffix}",
                branch_id=bid,
                is_active=True,
            )
            db.add(u)
            users.append(u)
            ids.append(u.id)
        q = WritingQuestion(
            id=str(uuid.uuid4()),
            public_id=f"M11-NEON-{tag.upper()}",
            test_type="academic",
            task_number=2,
            question_type="agree_disagree",
            topic="education",
            prompt="Some people think education is the most important factor in life.",
            minimum_words=250,
            recommended_minutes=40,
            status="published",
            is_permanent_bank=True,
        )
        db.add(q)
        db.commit()
        qid = q.id
        stu_a, stu_b, tea_a, tea_b = ids
    finally:
        db.close()

    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"m11_a_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"m11_b_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_ta = client.post(
        "/api/auth/login",
        json={"username": f"m11_ta_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]
    tok_tb = client.post(
        "/api/auth/login",
        json={"username": f"m11_tb_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]

    print("=== Catalog / start / draft ===")
    cat = client.get("/api/writing/catalog", headers=_hdr(tok_a), params={"test_type": "academic"})
    check(cat.status_code == 200, "catalog with JWT")
    start = client.post(
        f"/api/writing/questions/M11-NEON-{tag.upper()}/start",
        headers={**_hdr(tok_a), "X-Student-Id": stu_b},
    )
    check(start.status_code == 200, f"start ({start.status_code})")
    body = start.json() if start.status_code == 200 else {}
    attempt_id = body.get("id") or ""
    if attempt_id:
        attempt_ids.append(attempt_id)

    db = SessionLocal()
    try:
        row = db.query(WritingAttempt).filter_by(id=attempt_id).first()
        check(row is not None and row.student_id == stu_a, "DB attempt owned by Student A")
    finally:
        db.close()

    essay = "Education transforms life opportunities across generations and societies. " * 12
    save = client.patch(
        f"/api/writing/attempts/{attempt_id}",
        headers=_hdr(tok_a),
        json={"answer_text": essay},
    )
    check(save.status_code == 200, "autosave")
    resumed = client.get(f"/api/writing/attempts/{attempt_id}", headers=_hdr(tok_a))
    check(resumed.status_code == 200 and "Education transforms" in resumed.json().get("answer_text", ""), "resume restores draft")

    print("=== IDOR ===")
    check(client.get(f"/api/writing/attempts/{attempt_id}", headers=_hdr(tok_b)).status_code == 403, "B cannot get A draft")
    check(
        client.post(
            f"/api/writing/attempts/{attempt_id}/submit",
            headers=_hdr(tok_b),
            json={"answer_text": essay},
        ).status_code
        == 403,
        "B cannot submit A draft",
    )

    print("=== Submit / mocked AI / teacher review ===")
    with patch(
        "app.writing.services.writing_ai.writing_chat_json",
        new=AsyncMock(return_value=_grading(6.0)),
    ):
        submit = client.post(
            f"/api/writing/attempts/{attempt_id}/submit",
            headers=_hdr(tok_a),
            json={"answer_text": essay},
        )
    check(submit.status_code == 200, f"submit ({submit.status_code})")
    result = submit.json() if submit.status_code == 200 else {}
    check(result.get("status") == "graded", f"status graded got {result.get('status')}")
    check(result.get("estimated_band") == 6.0, f"estimated_band=6.0 got {result.get('estimated_band')}")

    dup = client.post(
        f"/api/writing/attempts/{attempt_id}/submit",
        headers=_hdr(tok_a),
        json={"answer_text": "CHANGED"},
    )
    check(dup.status_code == 200 and "CHANGED" not in (dup.json().get("answer_text") or ""), "duplicate submit safe")

    review = client.post(
        f"/api/writing/admin/submissions/{attempt_id}/review",
        headers=_hdr(tok_ta),
        json={"teacher_band": 6.5, "teacher_comments": "Good structure.", "publish": True},
    )
    check(review.status_code == 200 and review.json().get("teacher_band") == 6.5, "teacher A review")
    check(
        client.post(
            f"/api/writing/admin/submissions/{attempt_id}/review",
            headers=_hdr(tok_tb),
            json={"teacher_band": 9.0},
        ).status_code
        == 403,
        "teacher B cross-branch denied",
    )

    db = SessionLocal()
    try:
        row = db.query(WritingAttempt).filter_by(id=attempt_id).first()
        check(
            row is not None
            and row.status == "published"
            and row.estimated_band == 6.0
            and row.teacher_band == 6.5
            and row.submitted_text
            and "Education transforms" in row.submitted_text,
            "Neon persistence",
        )
    finally:
        db.close()

    detail = client.get(f"/api/writing/attempts/{attempt_id}", headers=_hdr(tok_a))
    check(detail.status_code == 200 and detail.json().get("final_band") == 6.5, "student sees final teacher band")
    check(client.get(f"/api/writing/attempts/{attempt_id}", headers=_hdr(tok_b)).status_code == 403, "B cannot read A result")

    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        for aid in attempt_ids:
            row = db.query(WritingAttempt).filter_by(id=aid).first()
            if row:
                db.delete(row)
        if qid:
            q = db.query(WritingQuestion).filter_by(id=qid).first()
            if q:
                db.delete(q)
        for uid in ids:
            u = db.query(User).filter_by(id=uid).first()
            if u:
                db.delete(u)
        for bid in (branch_a, branch_b):
            br = db.query(Branch).filter_by(id=bid).first()
            if br:
                db.delete(br)
        db.commit()
        print("  cleaned M11 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM11_NEON_WRITING_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM11_NEON_WRITING_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
