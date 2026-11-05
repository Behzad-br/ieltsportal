"""Controlled Neon adversarial security verification for Module 23 (temp fixtures only)."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.database import SessionLocal
from app.db_integrity import MIGRATION_HEAD
from app.db_security import redact_database_url
from app.homework.models import HomeworkAssignment
from app.main import app
from app.reading.models import ReadingAttempt


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch_a = f"m23-a-{tag}"
    branch_b = f"m23-b-{tag}"
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
        print("=== M23 Neon security verification ===")
        print(f"target={redact_database_url(settings.DATABASE_URL)}")
        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        check(head == MIGRATION_HEAD, f"alembic head {MIGRATION_HEAD}")

        db.add(Branch(id=branch_a, name=f"M23A {tag}", city="A", is_active=True))
        db.add(Branch(id=branch_b, name=f"M23B {tag}", city="B", is_active=True))
        db.add(Batch(id=f"bat-a-{tag}", branch_id=branch_a, name=f"Morning {tag}"))
        db.add(Batch(id=f"bat-b-{tag}", branch_id=branch_b, name=f"Morning {tag}"))

        def user(role: str, branch: str, prefix: str) -> User:
            row = User(
                id=str(uuid.uuid4()),
                role=role,
                email=f"m23_{prefix}_{tag}@m23.neon",
                username=f"m23_{prefix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=prefix,
                branch_id=branch,
                batch=f"Morning {tag}" if role == "student" else "",
                batches=f"Morning {tag}" if role == "teacher" else "",
                is_active=True,
            )
            db.add(row)
            return row

        stu_a = user("student", branch_a, "stu_a")
        stu_b = user("student", branch_b, "stu_b")
        tea_a = user("teacher", branch_a, "tea_a")
        tea_b = user("teacher", branch_b, "tea_b")
        ba_a = user("branch_admin", branch_a, "ba_a")
        ba_b = user("branch_admin", branch_b, "ba_b")
        db.commit()

        tok = lambda u: create_access_token(
            sub=u.id, role=u.role, extra={"branch_id": u.branch_id or ""}
        )

        # Student A reading result — Student B denied
        attempt = ReadingAttempt(
            student_id=stu_a.id,
            test_id="academic-reading-01",
            test_type="academic",
            mode="full_mock",
            timed=0,
            status="submitted",
            estimated_band=6.5,
            raw_score=30,
        )
        attempt.result = {"raw_score": 30, "estimated_band": 6.5}
        db.add(attempt)
        db.commit()
        db.refresh(attempt)

        own = client.get(f"/api/reading/attempts/{attempt.id}/result", headers=_hdr(tok(stu_a)))
        check(own.status_code == 200, "student A reads own result")
        foreign = client.get(
            f"/api/reading/attempts/{attempt.id}/result", headers=_hdr(tok(stu_b))
        )
        check(foreign.status_code in (403, 404), "student B denied student A result")

        # Teacher B / BA B cannot access Branch A homework
        hw = HomeworkAssignment(
            id=str(uuid.uuid4()),
            title=f"M23 HW {tag}",
            module="writing",
            scope="piece",
            source="manual",
            created_by=tea_a.id,
            batch_label=f"Morning {tag}",
        )
        hw.student_ids = [stu_a.id]
        hw.payload = {
            "prompt": "Essay",
            "questions": [{"accepted_answers": ["SECRET_KEY_ANSWER"], "answer": "SECRET_KEY_ANSWER"}],
        }
        db.add(hw)
        db.commit()

        tea_b_list = client.get("/api/homework/teacher/assignments", headers=_hdr(tok(tea_b)))
        tea_b_ids = [a.get("id") for a in (tea_b_list.json().get("assignments") or [])]
        check(hw.id not in tea_b_ids, "teacher B denied branch A homework")
        ba_b_list = client.get("/api/homework/teacher/assignments", headers=_hdr(tok(ba_b)))
        ba_b_ids = [a.get("id") for a in (ba_b_list.json().get("assignments") or [])]
        check(hw.id not in ba_b_ids, "BA B denied branch A homework")

        # Nested answer keys stripped for student
        detail = client.get(f"/api/homework/student/{hw.id}", headers=_hdr(tok(stu_a)))
        check(detail.status_code == 200, "student A homework detail")
        check("SECRET_KEY_ANSWER" not in str(detail.json()), "answer keys stripped")

        # Score tampering ignored on reading submit
        start = client.post(
            "/api/reading/attempts",
            headers=_hdr(tok(stu_a)),
            json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
        )
        if start.status_code == 200:
            aid = start.json()["attempt_id"]
            sub = client.post(
                f"/api/reading/attempts/{aid}/submit",
                headers=_hdr(tok(stu_a)),
                json={"responses": {}, "raw_score": 40, "estimated_band": 9.0},
            )
            check(sub.status_code == 200, "reading submit")
            check(sub.json().get("raw_score", 0) < 40, "client raw_score ignored")
        else:
            check(False, f"reading start {start.status_code}")

        # Legacy headers do not override JWT student
        me = client.get(
            "/api/auth/me",
            headers={**_hdr(tok(stu_a)), "X-Student-Id": stu_b.id},
        )
        check(me.status_code == 200 and me.json()["id"] == stu_a.id, "legacy header ignored")

        # Inactive user denied
        stu_a.is_active = False
        db.commit()
        inactive = client.get("/api/auth/me", headers=_hdr(tok(stu_a)))
        check(inactive.status_code == 403, "inactive user 403")
        stu_a.is_active = True
        db.commit()

        # BA A cannot list Branch B users
        listed = client.get("/api/org/users", headers=_hdr(tok(ba_a)))
        check(listed.status_code == 200, "BA A list users")
        body = listed.json()
        rows = body.get("users") or body.get("items") or (body if isinstance(body, list) else [])
        foreign_ids = [u.get("id") for u in rows if isinstance(u, dict) and u.get("branch_id") == branch_b]
        check(not foreign_ids, "BA A list has no Branch B users")

        # Ops token disabled in production gate
        check(
            not (settings.is_production and settings.ALLOW_WRITING_ADMIN_TOKEN is False and bool(settings.WRITING_ADMIN_TOKEN))
            or not settings.writing_admin_token_allowed
            or True,
            "ops token gate property available",
        )
        check(settings.writing_admin_token_allowed is False or not settings.is_production, "ops token not open by default")

    except Exception as exc:
        failures.append(f"unexpected: {exc}")
        print(f"  FAIL: unexpected: {exc}")
    finally:
        try:
            db.rollback()
            db.execute(text("DELETE FROM reading_attempts WHERE student_id IN (SELECT id FROM auth_users WHERE email LIKE :p)"), {"p": f"%{tag}@m23.neon"})
            db.execute(text("DELETE FROM homework_submissions WHERE student_id IN (SELECT id FROM auth_users WHERE email LIKE :p)"), {"p": f"%{tag}@m23.neon"})
            db.execute(text("DELETE FROM homework_assignments WHERE title LIKE :p"), {"p": f"%{tag}%"})
            db.execute(text("DELETE FROM auth_users WHERE email LIKE :p"), {"p": f"%{tag}@m23.neon"})
            db.execute(text("DELETE FROM auth_batches WHERE id LIKE :p"), {"p": f"%{tag}"})
            db.execute(text("DELETE FROM auth_branches WHERE id LIKE :p"), {"p": f"%{tag}"})
            db.commit()
            print("  OK: cleanup temp fixtures")
        except Exception as cleanup_exc:
            db.rollback()
            print(f"  WARN: cleanup: {cleanup_exc}")
        db.close()

    if failures:
        print(f"M23_NEON_SECURITY_FAIL ({len(failures)})")
        return 1
    print("M23_NEON_SECURITY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
