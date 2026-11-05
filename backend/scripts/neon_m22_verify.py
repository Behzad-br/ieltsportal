"""Controlled Neon failure-recovery verification for Module 22 (temp fixtures only)."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.database import SessionLocal
from app.db_integrity import MIGRATION_HEAD, scan_failure_states
from app.db_security import redact_database_url
from app.main import app
from app.writing.models import WritingAttempt, WritingQuestion
from app.writing.services import writing_service


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch = f"m22-neon-{tag}"
    failures: list[str] = []
    client = TestClient(app)
    db = SessionLocal()

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print(f"  FAIL: {msg}")
        else:
            print(f"  OK: {msg}")

    try:
        print("=== M22 Neon error / recovery verification ===")
        print(f"target={redact_database_url(settings.DATABASE_URL)}")
        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        check(head == MIGRATION_HEAD, f"alembic head {MIGRATION_HEAD}")

        db.add(Branch(id=branch, name=f"M22 {tag}", city="M22", is_active=True))
        db.add(Batch(id=f"bat-{tag}", branch_id=branch, name=f"Morning {tag}"))
        stu = User(
            id=str(uuid.uuid4()),
            role="student",
            email=f"m22_stu_{tag}@m22.neon",
            username=f"m22_stu_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M22 Student",
            branch_id=branch,
            batch=f"Morning {tag}",
            is_active=True,
        )
        sa = User(
            id=str(uuid.uuid4()),
            role="super_admin",
            email=f"m22_sa_{tag}@m22.neon",
            username=f"m22_sa_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M22 SA",
            branch_id="",
            is_active=True,
        )
        db.add(stu)
        db.add(sa)
        q = WritingQuestion(
            public_id=f"M22N-{tag.upper()}",
            test_type="academic",
            task_number=2,
            question_type="opinion",
            title="M22 Neon",
            prompt="Discuss education.",
            status="published",
            source_type="bank",
            minimum_words=150,
            recommended_minutes=40,
        )
        db.add(q)
        db.commit()
        db.refresh(q)

        sa_token = create_access_token(sub=sa.id, role="super_admin", extra={"branch_id": ""})
        stu_token = create_access_token(
            sub=stu.id, role="student", extra={"branch_id": branch}
        )

        # Uniqueness conflict
        body = {
            "role": "student",
            "email": f"m22_dup_{tag}@m22.neon",
            "username": f"m22_dup_{tag}",
            "password": "Passw0rd!",
            "full_name": "Dup",
            "branch_id": branch,
            "batch": f"Morning {tag}",
        }
        first = client.post("/api/org/users", headers=_hdr(sa_token), json=body)
        check(first.status_code == 200, "create user 200")
        second = client.post("/api/org/users", headers=_hdr(sa_token), json=body)
        check(second.status_code == 409, "duplicate user 409")

        # Transaction rollback: commit=False skill start then abort
        attempt = writing_service.start_attempt(db, stu.id, q, commit=False)
        attempt_id = attempt.id
        db.rollback()
        gone = db.query(WritingAttempt).filter_by(id=attempt_id).first()
        check(gone is None, "rollback removes uncommitted writing attempt")

        # Idempotent reading submit
        start = client.post(
            "/api/reading/attempts",
            headers=_hdr(stu_token),
            json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
        )
        if start.status_code == 200:
            aid = start.json()["attempt_id"]
            s1 = client.post(
                f"/api/reading/attempts/{aid}/submit",
                headers=_hdr(stu_token),
                json={"responses": {}},
            )
            s2 = client.post(
                f"/api/reading/attempts/{aid}/submit",
                headers=_hdr(stu_token),
                json={"responses": {"1": "X"}},
            )
            check(s1.status_code == 200 and s2.status_code == 200, "reading submit idempotent")
            check(
                s1.json().get("raw_score") == s2.json().get("raw_score"),
                "retry does not change score",
            )
        else:
            check(False, f"reading start ({start.status_code})")

        # AI failure preserves essay
        settings.WRITING_AI_GRADING_ENABLED = True
        wstart = client.post(
            f"/api/writing/questions/{q.public_id}/start",
            headers=_hdr(stu_token),
        )
        check(wstart.status_code == 200, "writing start")
        essay = "Education remains vital for social mobility worldwide. " * 12
        with patch(
            "app.writing.services.writing_ai.writing_chat_json",
            new=AsyncMock(side_effect=RuntimeError("api_key=sk-NEONLEAK timeout")),
        ):
            sub = client.post(
                f"/api/writing/attempts/{wstart.json()['id']}/submit",
                headers=_hdr(stu_token),
                json={"answer_text": essay},
            )
        check(sub.status_code == 200, "writing submit with AI failure returns 200")
        body = sub.json()
        check(body.get("status") == "grading", "status remains grading (work preserved)")
        check("Education remains" in (body.get("answer_text") or ""), "essay preserved")
        err = body.get("grading_error") or ""
        check("sk-NEONLEAK" not in err, "grading_error sanitized")

        # Invalid linked note upload
        tea = User(
            id=str(uuid.uuid4()),
            role="teacher",
            email=f"m22_tea_{tag}@m22.neon",
            username=f"m22_tea_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M22 Tea",
            branch_id=branch,
            batches=f"Morning {tag}",
            is_active=True,
        )
        db.add(tea)
        db.commit()
        tea_token = create_access_token(
            sub=tea.id, role="teacher", extra={"branch_id": branch}
        )
        bad_note = client.post(
            "/api/notes/teacher",
            headers=_hdr(tea_token),
            data={"title": "Bad", "description": ""},
            files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
        )
        check(bad_note.status_code in (400, 422), "invalid note upload rejected")

        # Diagnostics stay read-only
        report = scan_failure_states(db, limit=10)
        check(isinstance(report, dict) and "writing_grading_stuck" in report, "failure scan readonly")

        # Request id header
        health = client.get("/api/health", headers={"X-Request-ID": f"m22-{tag}"})
        check(
            health.status_code == 200 and health.headers.get("x-request-id") == f"m22-{tag}",
            "X-Request-ID echoed",
        )

    except Exception as exc:
        failures.append(f"unexpected: {exc}")
        print(f"  FAIL: unexpected: {exc}")
    finally:
        # Cleanup temp fixtures
        try:
            db.rollback()
            db.execute(
                text("DELETE FROM writing_attempts WHERE student_id IN (SELECT id FROM auth_users WHERE email LIKE :p)"),
                {"p": f"%{tag}@m22.neon"},
            )
            db.execute(
                text("DELETE FROM reading_attempts WHERE student_id IN (SELECT id FROM auth_users WHERE email LIKE :p)"),
                {"p": f"%{tag}@m22.neon"},
            )
            db.execute(
                text("DELETE FROM writing_questions WHERE public_id = :pid"),
                {"pid": f"M22N-{tag.upper()}"},
            )
            db.execute(text("DELETE FROM auth_users WHERE email LIKE :p"), {"p": f"%{tag}@m22.neon"})
            db.execute(text("DELETE FROM auth_batches WHERE id = :id"), {"id": f"bat-{tag}"})
            db.execute(text("DELETE FROM auth_branches WHERE id = :id"), {"id": branch})
            db.commit()
            print("  OK: cleanup temp fixtures")
        except Exception as cleanup_exc:
            db.rollback()
            print(f"  WARN: cleanup: {cleanup_exc}")
        db.close()

    if failures:
        print(f"M22_NEON_ERROR_FAIL ({len(failures)})")
        return 1
    print("M22_NEON_ERROR_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
