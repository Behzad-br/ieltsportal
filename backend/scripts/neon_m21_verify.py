"""Controlled Neon API-contract verification for Module 21 (temp fixtures only)."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.database import SessionLocal
from app.db_integrity import MIGRATION_HEAD
from app.db_security import redact_database_url
from app.main import app
from sqlalchemy import text


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch = f"m21-neon-{tag}"
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
        print("=== M21 Neon API contract verification ===")
        print(f"target={redact_database_url(settings.DATABASE_URL)}")
        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        check(head == MIGRATION_HEAD, f"alembic head {MIGRATION_HEAD}")

        db.add(Branch(id=branch, name=f"M21 {tag}", city="M21", is_active=True))
        db.add(Batch(id=f"bat-{tag}", branch_id=branch, name="Morning", teacher_id=""))
        stu = User(
            id=str(uuid.uuid4()),
            role="student",
            email=f"m21_stu_{tag}@m21.neon",
            username=f"m21_stu_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M21 Student",
            branch_id=branch,
            batch="Morning",
            is_active=True,
        )
        tea = User(
            id=str(uuid.uuid4()),
            role="teacher",
            email=f"m21_tea_{tag}@m21.neon",
            username=f"m21_tea_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M21 Teacher",
            branch_id=branch,
            batches="Morning",
            is_active=True,
        )
        ba = User(
            id=str(uuid.uuid4()),
            role="branch_admin",
            email=f"m21_ba_{tag}@m21.neon",
            username=f"m21_ba_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M21 BA",
            branch_id=branch,
            is_active=True,
        )
        db.add(stu)
        db.add(tea)
        db.add(ba)
        db.query(Batch).filter_by(id=f"bat-{tag}").update({"teacher_id": tea.id})
        db.commit()

        # Login / me
        login = client.post(
            "/api/auth/login",
            json={"username": tea.username, "password": "Passw0rd!"},
        )
        check(login.status_code == 200, "teacher login 200")
        token = (login.json() or {}).get("access_token") or create_access_token(
            sub=tea.id, role="teacher", extra={"branch_id": branch}
        )
        ba_tok = create_access_token(sub=ba.id, role="branch_admin", extra={"branch_id": branch})
        me = client.get("/api/auth/me", headers=_hdr(token))
        check(me.status_code == 200 and me.json().get("id") == tea.id, "auth/me identity")

        # Unauthenticated
        check(client.get("/api/auth/me").status_code == 401, "unauth me -> 401")

        # Batch conflict (BA only)
        dup = client.post(
            "/api/org/batches",
            headers=_hdr(ba_tok),
            json={"name": "Morning", "branch_id": branch},
        )
        check(dup.status_code == 409, f"duplicate batch -> 409 (got {dup.status_code})")

        # Student skill start (writing question if bank empty — use homework manual)
        stu_tok = create_access_token(sub=stu.id, role="student", extra={"branch_id": branch})
        hw = client.post(
            "/api/homework/teacher/assignments",
            headers=_hdr(token),
            json={
                "title": f"M21 HW {tag}",
                "module": "writing",
                "source": "manual",
                "payload": {"prompt": "Neon M21."},
                "student_ids": [stu.id],
                "assignment_target": "specific",
            },
        )
        check(hw.status_code == 200, "homework create")
        hw_id = (hw.json() or {}).get("id")
        detail = client.get(f"/api/homework/student/{hw_id}", headers=_hdr(stu_tok))
        check(detail.status_code == 200, "student homework detail")
        body = detail.json() or {}
        check(
            body.get("teacher_band") is None,
            "unpublished teacher_band is null",
        )
        check("result_pending" in body or body.get("status") in ("pending", "in_progress"), "result fields present")

        # Analytics
        an = client.get("/api/analytics/teacher", headers=_hdr(token))
        check(an.status_code == 200, f"analytics teacher 200 (got {an.status_code})")

        # Cross-user reading attempt (foreign → 403/404)
        from app.reading.models import ReadingAttempt

        row = ReadingAttempt(
            student_id=stu.id,
            test_id="m21-probe",
            test_type="academic",
            status="in_progress",
        )
        db.add(row)
        db.commit()
        other = User(
            id=str(uuid.uuid4()),
            role="student",
            email=f"m21_o_{tag}@m21.neon",
            username=f"m21_o_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="Other",
            branch_id=branch,
            batch="Morning",
            is_active=True,
        )
        db.add(other)
        db.commit()
        o_tok = create_access_token(sub=other.id, role="student", extra={"branch_id": branch})
        denied = client.get(f"/api/reading/attempts/{row.id}", headers=_hdr(o_tok))
        check(
            denied.status_code in (403, 404),
            f"cross-user attempt hidden ({denied.status_code})",
        )

        # Cleanup
        db.query(ReadingAttempt).filter_by(id=row.id).delete()
        if hw_id:
            from app.homework.models import HomeworkAssignment, HomeworkSubmission

            db.query(HomeworkSubmission).filter_by(assignment_id=hw_id).delete()
            db.query(HomeworkAssignment).filter_by(id=hw_id).delete()
        db.query(User).filter(User.id.in_([stu.id, tea.id, ba.id, other.id])).delete(
            synchronize_session=False
        )
        db.query(Batch).filter_by(id=f"bat-{tag}").delete()
        db.query(Branch).filter_by(id=branch).delete()
        db.commit()
        check(True, "fixtures cleaned")

        if failures:
            print(f"=== M21 FAILED ({len(failures)}) ===")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("M21_NEON_API_PASS")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"FAIL: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
