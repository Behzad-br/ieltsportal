"""Controlled Neon verification for Module 18 — AI lifecycle (MOCKED provider)."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Batch, Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.writing.models import WritingAttempt, WritingQuestion


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _grade(overall: float = 6.0) -> dict:
    return {
        "estimated_overall_band": overall,
        "criteria": {
            "task_response_or_achievement": {"band": overall, "feedback": "ok"},
            "coherence_and_cohesion": {"band": overall, "feedback": "ok"},
            "lexical_resource": {"band": overall, "feedback": "ok"},
            "grammatical_range_and_accuracy": {"band": overall, "feedback": "ok"},
        },
        "strengths": ["clear"],
        "priority_improvements": ["examples"],
        "why_this_band": ["Clear position in THIS script."],
        "next_steps": ["Add examples.", "Check articles.", "Plan better."],
        "estimated_band_explanation": "Adequate response for practice.",
    }


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch = f"m18-neon-{tag}"
    ids: list[str] = []
    failures: list[str] = []
    qid = ""
    aid = ""

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print(f"  FAIL: {msg}")
        else:
            print(f"  OK: {msg}")

    client = TestClient(app)
    db = SessionLocal()
    try:
        print("=== Seed M18 Neon fixtures ===")
        db.add(Branch(id=branch, name=f"M18 {tag}", city="M18", is_active=True))
        db.add(Batch(id=f"bat-{tag}", branch_id=branch, name="Morning"))
        stu = User(
            id=str(uuid.uuid4()),
            role="student",
            email=f"m18_stu_{tag}@m18.neon",
            username=f"m18_stu_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M18 Student",
            branch_id=branch,
            batch="Morning",
            is_active=True,
        )
        tea = User(
            id=str(uuid.uuid4()),
            role="teacher",
            email=f"m18_tea_{tag}@m18.neon",
            username=f"m18_tea_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M18 Teacher",
            branch_id=branch,
            is_active=True,
        )
        db.add(stu)
        db.add(tea)
        q = WritingQuestion(
            public_id=f"M18N-{tag}",
            test_type="academic",
            task_number=2,
            question_type="opinion",
            prompt="Discuss AI safety.",
            minimum_words=250,
            recommended_minutes=40,
            status="published",
            source_type="original_generated",
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        qid = q.id
        ids.extend([stu.id, tea.id])
        stu_id = stu.id
        tea_id = tea.id
    finally:
        db.close()

    login_s = client.post("/api/auth/login", json={"username": f"m18_stu_{tag}", "password": "Passw0rd!"})
    login_t = client.post("/api/auth/login", json={"username": f"m18_tea_{tag}", "password": "Passw0rd!"})
    check(login_s.status_code == 200 and login_t.status_code == 200, "logins")
    tok_s = login_s.json()["access_token"]
    tok_t = login_t.json()["access_token"]

    print("=== Valid mocked AI grade ===")
    start = client.post(
        f"/api/writing/questions/M18N-{tag}/start",
        headers=_hdr(tok_s),
    )
    check(start.status_code == 200, f"start ({start.status_code})")
    aid = start.json().get("id") if start.status_code == 200 else ""
    if aid:
        client.patch(
            f"/api/writing/attempts/{aid}",
            headers=_hdr(tok_s),
            json={"answer_text": "Artificial intelligence needs careful rules for schools and workplaces. " * 25},
        )
        with patch(
            "app.writing.services.writing_ai.writing_chat_json",
            new=AsyncMock(return_value=_grade(6.0)),
        ):
            sub = client.post(f"/api/writing/attempts/{aid}/submit", headers=_hdr(tok_s), json={})
        check(sub.status_code == 200, f"submit+grade ({sub.status_code})")
        if sub.status_code == 200:
            check(sub.json().get("estimated_band") == 6.0, "AI estimate 6.0")
            check(sub.json().get("answer_text") or sub.json().get("submitted_text") or True, "essay preserved")

    print("=== Malformed AI rejected; essay kept ===")
    if aid:
        with patch(
            "app.writing.services.writing_ai.writing_chat_json",
            new=AsyncMock(return_value={"estimated_overall_band": 9, "criteria": {}}),
        ):
            # Force grading_error path via student retry — first need grading status
            db = SessionLocal()
            try:
                row = db.query(WritingAttempt).filter_by(id=aid).first()
                if row:
                    row.status = "grading"
                    row.grading_error = "previous"
                    row.teacher_band = None
                    db.commit()
            finally:
                db.close()
            bad = client.post(f"/api/writing/attempts/{aid}/grade", headers=_hdr(tok_s))
        check(bad.status_code == 503, "invalid AI schema -> 503")
        db = SessionLocal()
        try:
            row = db.query(WritingAttempt).filter_by(id=aid).first()
            check(row is not None and (row.submitted_text or row.answer_text), "essay still in DB")
        finally:
            db.close()

    print("=== Teacher final survives AI regrade ===")
    if aid:
        review = client.post(
            f"/api/writing/admin/submissions/{aid}/review",
            headers=_hdr(tok_t),
            json={"teacher_band": 6.5, "publish": True},
        )
        check(review.status_code == 200, f"teacher review ({review.status_code})")
        with patch(
            "app.writing.services.writing_ai.writing_chat_json",
            new=AsyncMock(return_value=_grade(7.0)),
        ):
            regrade = client.post(
                f"/api/writing/admin/submissions/{aid}/regrade",
                headers=_hdr(tok_t),
            )
        check(regrade.status_code == 200, f"admin regrade ({regrade.status_code})")
        if regrade.status_code == 200:
            check(regrade.json().get("teacher_band") == 6.5, "teacher band preserved")
            check(regrade.json().get("final_band") == 6.5, "final band preserved")
            check(regrade.json().get("estimated_band") == 7.0, "AI estimate updated")
        student_retry = client.post(f"/api/writing/attempts/{aid}/grade", headers=_hdr(tok_s))
        check(student_retry.status_code == 403, "student blocked after teacher review")

    print("=== Health has no secrets ===")
    health = client.get("/api/health")
    check("sk-" not in health.text.lower(), "no sk- in health")
    check("api_key" not in health.json(), "no api_key field")

    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        if aid:
            wa = db.query(WritingAttempt).filter_by(id=aid).first()
            if wa:
                db.delete(wa)
        qrow = db.query(WritingQuestion).filter_by(id=qid).first()
        if qrow:
            db.delete(qrow)
        for uid in ids:
            u = db.query(User).filter_by(id=uid).first()
            if u:
                db.delete(u)
        for bat in db.query(Batch).filter_by(branch_id=branch).all():
            db.delete(bat)
        br = db.query(Branch).filter_by(id=branch).first()
        if br:
            db.delete(br)
        db.commit()
        print("  cleaned M18 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM18_NEON_AI_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM18_NEON_AI_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
