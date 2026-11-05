"""Module 24 — Neon final E2E / production readiness verification (temp fixtures only).

Creates Branch M24-A/B + roles, exercises org → skills → homework → notes → mock →
security denials, then cleans fixtures with exact M24 identity prefix.
"""
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
from app.db_integrity import MIGRATION_HEAD
from app.db_security import redact_database_url
from app.homework.models import HomeworkAssignment
from app.main import app
from app.notes.models import ClassNote
from app.writing.models import WritingQuestion


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch_a = f"m24-a-{tag}"
    branch_b = f"m24-b-{tag}"
    batch_a = f"Morning {tag}"
    batch_b = f"Evening {tag}"
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
        print("=== M24 Neon E2E / production readiness ===")
        print(f"target={redact_database_url(settings.DATABASE_URL)}")

        # Health
        health = client.get("/api/health")
        check(health.status_code == 200, "GET /api/health 200")
        h = health.json()
        check(h.get("database") is True, "health.database true")
        check("password" not in str(h).lower(), "health has no secrets")
        check("ffmpeg_available" in h, "health includes ffmpeg_available")

        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        check(head == MIGRATION_HEAD, f"alembic current == {MIGRATION_HEAD}")

        # Org fixtures
        db.add(Branch(id=branch_a, name=f"M24-A {tag}", city="A", is_active=True))
        db.add(Branch(id=branch_b, name=f"M24-B {tag}", city="B", is_active=True))
        db.add(Batch(id=f"bat-a-{tag}", branch_id=branch_a, name=batch_a))
        db.add(Batch(id=f"bat-b-{tag}", branch_id=branch_b, name=batch_b))

        def mk(role: str, branch: str, prefix: str, *, batch: str = "") -> User:
            row = User(
                id=str(uuid.uuid4()),
                role=role,
                email=f"m24_{prefix}_{tag}@m24.neon",
                username=f"m24_{prefix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=prefix,
                branch_id="" if role == "super_admin" else branch,
                batch=batch if role == "student" else "",
                batches=batch if role == "teacher" else "",
                is_active=True,
            )
            db.add(row)
            return row

        sa = mk("super_admin", "", "sa")
        ba_a = mk("branch_admin", branch_a, "ba_a")
        ba_b = mk("branch_admin", branch_b, "ba_b")
        tea_a = mk("teacher", branch_a, "tea_a", batch=batch_a)
        tea_b = mk("teacher", branch_b, "tea_b", batch=batch_b)
        stu_a = mk("student", branch_a, "stu_a", batch=batch_a)
        stu_b = mk("student", branch_b, "stu_b", batch=batch_b)
        q = WritingQuestion(
            public_id=f"M24Q-{tag.upper()}",
            test_type="academic",
            task_number=2,
            question_type="opinion",
            title="M24 Essay",
            prompt="Discuss education.",
            status="published",
            source_type="bank",
            minimum_words=150,
            recommended_minutes=40,
        )
        db.add(q)
        db.commit()
        db.refresh(q)

        def tok(u: User) -> str:
            return create_access_token(
                sub=u.id, role=u.role, extra={"branch_id": u.branch_id or ""}
            )

        # Auth four roles
        for u, role in ((sa, "super_admin"), (ba_a, "branch_admin"), (tea_a, "teacher"), (stu_a, "student")):
            me = client.get("/api/auth/me", headers=_hdr(tok(u)))
            check(me.status_code == 200 and me.json().get("role") == role, f"auth/me {role}")

        # SA analytics
        sa_an = client.get("/api/analytics/super-admin", headers=_hdr(tok(sa)))
        check(sa_an.status_code == 200, "SA analytics 200")

        # BA isolation
        ba_users = client.get("/api/org/users", headers=_hdr(tok(ba_a)))
        check(ba_users.status_code == 200, "BA A list users")
        rows = ba_users.json().get("users") or ba_users.json().get("items") or []
        check(
            all((r.get("branch_id") or "") in ("", branch_a) for r in rows if isinstance(r, dict)),
            "BA A users scoped to Branch A",
        )
        denied_patch = client.patch(
            f"/api/org/users/{stu_b.id}",
            headers=_hdr(tok(ba_a)),
            json={"full_name": "Hacked"},
        )
        check(denied_patch.status_code in (403, 404), "BA A cannot patch Branch B student")

        # Reading E2E
        r_start = client.post(
            "/api/reading/attempts",
            headers=_hdr(tok(stu_a)),
            json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
        )
        check(r_start.status_code == 200, "Reading start")
        rid = r_start.json()["attempt_id"]
        client.patch(
            f"/api/reading/attempts/{rid}/responses",
            headers=_hdr(tok(stu_a)),
            json={"responses": {"1": "TRUE"}},
        )
        r_sub = client.post(
            f"/api/reading/attempts/{rid}/submit",
            headers=_hdr(tok(stu_a)),
            json={"responses": {"1": "TRUE"}, "raw_score": 40, "estimated_band": 9.0},
        )
        check(r_sub.status_code == 200, "Reading submit")
        check(r_sub.json().get("raw_score", 99) < 40, "Reading client score ignored")
        check(
            client.get(f"/api/reading/attempts/{rid}/result", headers=_hdr(tok(stu_b))).status_code
            in (403, 404),
            "Student B denied Reading result",
        )

        # Listening E2E
        from app.listening.services import bank as listening_bank

        l_catalog = listening_bank.catalog()
        tests = (l_catalog or {}).get("tests") or []
        tid = (tests[0].get("id") if tests else None) or "listening-test-01"
        l_start = client.post(
            "/api/listening/attempts",
            headers=_hdr(tok(stu_a)),
            json={"test_id": tid, "mode": "full_mock", "timed": False},
        )
        lid_ok = False
        if l_start.status_code == 200:
            lid = l_start.json().get("attempt_id") or l_start.json().get("id")
            client.patch(
                f"/api/listening/attempts/{lid}/responses",
                headers=_hdr(tok(stu_a)),
                json={"responses": {"1": "museum"}},
            )
            l_sub = client.post(
                f"/api/listening/attempts/{lid}/submit",
                headers=_hdr(tok(stu_a)),
                json={"responses": {"1": "museum"}},
            )
            lid_ok = l_sub.status_code == 200
            if lid_ok:
                check(
                    client.get(
                        f"/api/listening/attempts/{lid}/result",
                        headers=_hdr(tok(stu_b)),
                    ).status_code
                    in (403, 404),
                    "Student B denied Listening result",
                )
        check(lid_ok, f"Listening start/submit (test_id={tid})")

        # Writing E2E (AI mocked)
        settings.WRITING_AI_GRADING_ENABLED = True
        w_start = client.post(
            f"/api/writing/questions/{q.public_id}/start",
            headers=_hdr(tok(stu_a)),
        )
        check(w_start.status_code == 200, "Writing start")
        wid = w_start.json()["id"]
        essay = "Education remains essential for social mobility and national growth. " * 12
        client.patch(
            f"/api/writing/attempts/{wid}",
            headers=_hdr(tok(stu_a)),
            json={"answer_text": essay},
        )
        with patch(
            "app.writing.services.writing_ai.writing_chat_json",
            new=AsyncMock(side_effect=RuntimeError("provider down")),
        ):
            w_sub = client.post(
                f"/api/writing/attempts/{wid}/submit",
                headers=_hdr(tok(stu_a)),
                json={"answer_text": essay},
            )
        check(w_sub.status_code == 200, "Writing submit survives AI failure")
        check(w_sub.json().get("status") == "grading", "Writing status grading after AI fail")
        check("Education remains" in (w_sub.json().get("answer_text") or ""), "Essay preserved")

        # Speaking E2E (create session via service path used by API)
        from app.speaking.services import speaking_session
        from app.speaking.services import question_bank

        test = question_bank.get_random_test() or {
            "id": "m24-sp",
            "title": "M24 Speaking",
            "part1": ["Tell me about your hometown."],
            "part2": {"topic": "A memorable day", "bullets": ["When", "Where", "Why"]},
            "part3": ["Why do people travel?"],
        }
        sess, _ = speaking_session.create_session(
            db, "stored", test, student_id=stu_a.id, commit=True
        )
        check(bool(sess.id), "Speaking session created")
        check(
            client.get(f"/api/history/{sess.id}", headers=_hdr(tok(stu_b))).status_code
            in (403, 404),
            "Student B denied speaking history",
        )

        # Homework
        hw = HomeworkAssignment(
            id=str(uuid.uuid4()),
            title=f"M24 HW {tag}",
            module="writing",
            scope="piece",
            source="manual",
            created_by=tea_a.id,
            batch_label=batch_a,
        )
        hw.student_ids = [stu_a.id]
        hw.payload = {
            "prompt": "Write",
            "questions": [{"accepted_answers": ["SECRET_KEY"], "answer": "SECRET_KEY"}],
        }
        db.add(hw)
        db.commit()
        detail = client.get(f"/api/homework/student/{hw.id}", headers=_hdr(tok(stu_a)))
        check(detail.status_code == 200, "Student homework detail")
        check("SECRET_KEY" not in str(detail.json()), "Homework keys stripped")
        tea_b_list = client.get("/api/homework/teacher/assignments", headers=_hdr(tok(tea_b)))
        check(
            hw.id not in [a.get("id") for a in (tea_b_list.json().get("assignments") or [])],
            "Teacher B cannot see Branch A homework",
        )

        # Notes
        note = ClassNote(
            id=str(uuid.uuid4()),
            title=f"M24 Note {tag}",
            description="Class notes",
            original_name="n.pdf",
            stored_name=f"m24-{tag}.pdf",
            page_count=1,
            created_by=tea_a.id,
        )
        note.unlocked_batches = [batch_a]
        note.unlocked_student_ids = []
        db.add(note)
        # create empty file so path exists for ACL tests
        Path(settings.NOTES_DIR).mkdir(parents=True, exist_ok=True)
        (Path(settings.NOTES_DIR) / note.stored_name).write_bytes(b"%PDF-1.4 m24")
        db.commit()
        notes_a = client.get("/api/notes/student", headers=_hdr(tok(stu_a)))
        check(any(n["id"] == note.id for n in notes_a.json().get("notes") or []), "Student A sees note")
        notes_b = client.get("/api/notes/student", headers=_hdr(tok(stu_b)))
        check(
            not any(n["id"] == note.id for n in notes_b.json().get("notes") or []),
            "Student B does not see Branch A note",
        )

        # Mock assign/start (single skill)
        from app.mocks.models import MockAssignment, MockAssignmentStudent

        mock = MockAssignment(
            id=str(uuid.uuid4()),
            title=f"M24 Mock {tag}",
            mock_type="writing",
            ielts_type="academic",
            created_by=tea_a.id,
            duration_minutes=40,
            paper_source="manual",
        )
        mock.paper_refs = {"prompt": "Discuss transport.", "question_id": q.id}
        db.add(mock)
        db.flush()
        slot = MockAssignmentStudent(
            id=str(uuid.uuid4()),
            assignment_id=mock.id,
            student_id=stu_a.id,
            student_name=stu_a.full_name,
        )
        db.add(slot)
        db.commit()
        inbox = client.get("/api/mocks/student/inbox", headers=_hdr(tok(stu_a)))
        check(inbox.status_code == 200, "Mock inbox")
        check(
            any(i.get("id") == mock.id for i in (inbox.json().get("items") or [])),
            "Student A sees assigned mock",
        )

        # Analytics student
        stu_an = client.get("/api/analytics/student", headers=_hdr(tok(stu_a)))
        check(stu_an.status_code == 200, "Student analytics")

        # Inactive user
        stu_a.is_active = False
        db.commit()
        check(
            client.get("/api/auth/me", headers=_hdr(tok(stu_a))).status_code == 403,
            "Inactive student denied",
        )
        stu_a.is_active = True
        db.commit()

    except Exception as exc:
        failures.append(f"unexpected: {exc}")
        print(f"  FAIL: unexpected: {exc}")
    finally:
        try:
            db.rollback()
            # Cleanup note file
            for p in Path(settings.NOTES_DIR).glob(f"m24-{tag}*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            db.execute(
                text(
                    "DELETE FROM reading_attempts WHERE student_id IN "
                    "(SELECT id FROM auth_users WHERE email LIKE :p)"
                ),
                {"p": f"%{tag}@m24.neon"},
            )
            db.execute(
                text(
                    "DELETE FROM listening_attempts WHERE student_id IN "
                    "(SELECT id FROM auth_users WHERE email LIKE :p)"
                ),
                {"p": f"%{tag}@m24.neon"},
            )
            db.execute(
                text(
                    "DELETE FROM writing_attempts WHERE student_id IN "
                    "(SELECT id FROM auth_users WHERE email LIKE :p)"
                ),
                {"p": f"%{tag}@m24.neon"},
            )
            db.execute(
                text(
                    "DELETE FROM answers WHERE session_id IN "
                    "(SELECT id FROM test_sessions WHERE student_id IN "
                    "(SELECT id FROM auth_users WHERE email LIKE :p))"
                ),
                {"p": f"%{tag}@m24.neon"},
            )
            db.execute(
                text(
                    "DELETE FROM questions WHERE session_id IN "
                    "(SELECT id FROM test_sessions WHERE student_id IN "
                    "(SELECT id FROM auth_users WHERE email LIKE :p))"
                ),
                {"p": f"%{tag}@m24.neon"},
            )
            db.execute(
                text(
                    "DELETE FROM test_sessions WHERE student_id IN "
                    "(SELECT id FROM auth_users WHERE email LIKE :p)"
                ),
                {"p": f"%{tag}@m24.neon"},
            )
            db.execute(
                text(
                    "DELETE FROM mock_assignment_students WHERE student_id IN "
                    "(SELECT id FROM auth_users WHERE email LIKE :p)"
                ),
                {"p": f"%{tag}@m24.neon"},
            )
            db.execute(text("DELETE FROM mock_assignments WHERE title LIKE :p"), {"p": f"%{tag}%"})
            db.execute(
                text("DELETE FROM homework_submissions WHERE student_id IN "
                     "(SELECT id FROM auth_users WHERE email LIKE :p)"),
                {"p": f"%{tag}@m24.neon"},
            )
            db.execute(text("DELETE FROM homework_assignments WHERE title LIKE :p"), {"p": f"%{tag}%"})
            db.execute(text("DELETE FROM class_notes WHERE title LIKE :p"), {"p": f"%{tag}%"})
            db.execute(text("DELETE FROM writing_questions WHERE public_id = :pid"), {"pid": f"M24Q-{tag.upper()}"})
            db.execute(text("DELETE FROM auth_users WHERE email LIKE :p"), {"p": f"%{tag}@m24.neon"})
            db.execute(text("DELETE FROM auth_batches WHERE id LIKE :p"), {"p": f"%{tag}"})
            db.execute(text("DELETE FROM auth_branches WHERE id LIKE :p"), {"p": f"%{tag}"})
            db.commit()
            print("  OK: cleanup temp fixtures")
        except Exception as cleanup_exc:
            db.rollback()
            print(f"  WARN: cleanup: {cleanup_exc}")
        db.close()

    if failures:
        print(f"M24_NEON_E2E_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("M24_NEON_E2E_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
