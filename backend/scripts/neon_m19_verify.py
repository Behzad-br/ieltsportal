"""Controlled Neon verification for Module 19 — database integrity (non-destructive)."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.auth.models import Batch, Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import Base, SessionLocal
from app.db_integrity import MIGRATION_HEAD, skill_ref_cross_user
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.mocks.models import MockAssignment, MockAssignmentStudent
from app.writing.models import WritingAttempt, WritingQuestion

# register models
import app.auth.models  # noqa: F401
import app.homework.models  # noqa: F401
import app.listening.models  # noqa: F401
import app.mocks.models  # noqa: F401
import app.notes.models  # noqa: F401
import app.reading.models  # noqa: F401
import app.speaking.models  # noqa: F401
import app.writing.models  # noqa: F401


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    tag = uuid.uuid4().hex[:8]
    branch = f"m19-neon-{tag}"
    failures: list[str] = []
    ids: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print(f"  FAIL: {msg}")
        else:
            print(f"  OK: {msg}")

    db = SessionLocal()
    try:
        print("=== M19 Neon database verification ===")
        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        check(head == MIGRATION_HEAD, f"alembic head is {MIGRATION_HEAD} (got {head})")

        insp = inspect(db.bind)
        live = set(insp.get_table_names())
        orm = set(Base.metadata.tables.keys())
        check(not (orm - live), f"all ORM tables present (missing {sorted(orm - live)})")
        check(not (live - orm - {"alembic_version", "migration_metadata"}), "no unexpected live tables")

        slot_ucs = {c["name"] for c in insp.get_unique_constraints("mock_assignment_students")}
        slot_idxs = {i["name"] for i in insp.get_indexes("mock_assignment_students") if i.get("unique")}
        check(
            "uq_mock_assignment_students_assignment_student" in slot_ucs
            or "uq_mock_assignment_students_assignment_student" in slot_idxs,
            "mock slot unique constraint present",
        )
        hw_ucs = {c["name"] for c in insp.get_unique_constraints("homework_submissions")}
        check("uq_homework_sub_assignment_student" in hw_ucs, "homework submission unique present")
        bat_ucs = {c["name"] for c in insp.get_unique_constraints("auth_batches")}
        check("uq_auth_batches_branch_name" in bat_ucs, "batch (branch_id,name) unique present")

        print("--- seed fixtures ---")
        db.add(Branch(id=branch, name=f"M19 {tag}", city="M19", is_active=True))
        db.add(Batch(id=f"bat-{tag}", branch_id=branch, name="Morning"))
        stu = User(
            id=str(uuid.uuid4()),
            role="student",
            email=f"m19_stu_{tag}@m19.neon",
            username=f"m19_stu_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M19 Student",
            branch_id=branch,
            batch="Morning",
            is_active=True,
        )
        tea = User(
            id=str(uuid.uuid4()),
            role="teacher",
            email=f"m19_tea_{tag}@m19.neon",
            username=f"m19_tea_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="M19 Teacher",
            branch_id=branch,
            batches="Morning",
            is_active=True,
        )
        db.add(stu)
        db.add(tea)
        ids.extend([branch, f"bat-{tag}", stu.id, tea.id])
        db.commit()
        check(True, "user/branch/batch fixtures created")

        # Uniqueness: duplicate batch
        try:
            db.add(Batch(id=f"bat-dup-{tag}", branch_id=branch, name="Morning"))
            db.commit()
            check(False, "duplicate batch rejected")
        except IntegrityError:
            db.rollback()
            check(True, "duplicate batch rejected")

        # Homework assignment + submission unique
        asg = HomeworkAssignment(
            id=str(uuid.uuid4()),
            title=f"M19 HW {tag}",
            module="writing",
            created_by=tea.id,
            batch_label="Morning",
        )
        asg.student_ids = [stu.id]
        db.add(asg)
        db.add(
            HomeworkSubmission(
                assignment_id=asg.id,
                student_id=stu.id,
                status="pending",
            )
        )
        db.commit()
        ids.append(asg.id)
        try:
            db.add(
                HomeworkSubmission(
                    assignment_id=asg.id,
                    student_id=stu.id,
                    status="pending",
                )
            )
            db.commit()
            check(False, "duplicate homework submission rejected")
        except IntegrityError:
            db.rollback()
            check(True, "duplicate homework submission rejected")

        # Mock slot unique
        mock = MockAssignment(
            id=str(uuid.uuid4()),
            title=f"M19 Mock {tag}",
            mock_type="writing",
            duration_minutes=30,
            created_by=tea.id,
            batch_label="Morning",
        )
        db.add(mock)
        db.add(MockAssignmentStudent(assignment_id=mock.id, student_id=stu.id))
        db.commit()
        ids.append(mock.id)
        try:
            db.add(MockAssignmentStudent(assignment_id=mock.id, student_id=stu.id))
            db.commit()
            check(False, "duplicate mock slot rejected")
        except IntegrityError:
            db.rollback()
            check(True, "duplicate mock slot rejected")

        # Cross-user skill_ref detection
        q = WritingQuestion(
            id=str(uuid.uuid4()),
            public_id=f"m19q-{tag}",
            test_type="academic",
            task_number=2,
            question_type="essay",
            prompt="Neon M19 probe.",
            status="published",
        )
        db.add(q)
        wa = WritingAttempt(
            id=str(uuid.uuid4()),
            student_id=stu.id,
            question_id=q.id,
            status="in_progress",
        )
        db.add(wa)
        db.commit()
        ids.extend([q.id, wa.id])
        other = str(uuid.uuid4())
        check(
            skill_ref_cross_user(db, other, {"kind": "writing-question", "ref": wa.id}),
            "cross-user skill_ref detected",
        )
        check(
            not skill_ref_cross_user(db, stu.id, {"kind": "writing-question", "ref": wa.id}),
            "same-user skill_ref accepted",
        )

        print("--- cleanup fixtures ---")
        for model, mid in (
            (WritingAttempt, wa.id),
            (WritingQuestion, q.id),
            (HomeworkSubmission, None),
            (HomeworkAssignment, asg.id),
            (MockAssignmentStudent, None),
            (MockAssignment, mock.id),
            (User, stu.id),
            (User, tea.id),
            (Batch, f"bat-{tag}"),
            (Branch, branch),
        ):
            pass
        db.query(HomeworkSubmission).filter_by(assignment_id=asg.id).delete()
        db.query(HomeworkAssignment).filter_by(id=asg.id).delete()
        db.query(MockAssignmentStudent).filter_by(assignment_id=mock.id).delete()
        db.query(MockAssignment).filter_by(id=mock.id).delete()
        db.query(WritingAttempt).filter_by(id=wa.id).delete()
        db.query(WritingQuestion).filter_by(id=q.id).delete()
        db.query(User).filter(User.id.in_([stu.id, tea.id])).delete(synchronize_session=False)
        db.query(Batch).filter_by(id=f"bat-{tag}").delete()
        db.query(Branch).filter_by(id=branch).delete()
        db.commit()
        check(True, "fixtures cleaned")

        if failures:
            print(f"=== M19 Neon FAILED ({len(failures)}) ===")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("M19_NEON_DATABASE_PASS")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
