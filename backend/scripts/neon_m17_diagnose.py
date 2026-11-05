"""Non-destructive Neon diagnostics for Module 17 analytics."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func

from app.analytics.bands import sanitize_band
from app.auth.models import Batch, Branch, User
from app.config import settings
from app.database import SessionLocal
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.listening.models import ListeningAttempt
from app.mocks.models import MockAttempt
from app.reading.models import ReadingAttempt
from app.speaking.models import TestSession
from app.writing.models import WritingAttempt


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    db = SessionLocal()
    try:
        print("=== M17 Neon analytics diagnose (read-only) ===")
        print(f"branches_total={db.query(func.count(Branch.id)).scalar()}")
        print(f"branches_active={db.query(func.count(Branch.id)).filter(Branch.is_active.is_(True)).scalar()}")
        print(f"students_active={db.query(func.count(User.id)).filter_by(role='student', is_active=True).scalar()}")
        print(f"students_inactive={db.query(func.count(User.id)).filter_by(role='student', is_active=False).scalar()}")
        print(f"teachers_active={db.query(func.count(User.id)).filter_by(role='teacher', is_active=True).scalar()}")
        print(f"batches={db.query(func.count(Batch.id)).scalar()}")
        print(f"reading_submitted={db.query(func.count(ReadingAttempt.id)).filter_by(status='submitted').scalar()}")
        print(f"listening_submitted={db.query(func.count(ListeningAttempt.id)).filter_by(status='submitted').scalar()}")
        print(
            f"writing_gradedish={db.query(func.count(WritingAttempt.id)).filter(WritingAttempt.status.in_(('graded','published','teacher_review'))).scalar()}"
        )
        print(f"speaking_completed={db.query(func.count(TestSession.id)).filter_by(status='completed').scalar()}")
        print(f"homework_assignments={db.query(func.count(HomeworkAssignment.id)).scalar()}")
        print(f"homework_submitted={db.query(func.count(HomeworkSubmission.id)).filter_by(status='submitted').scalar()}")
        print(f"mock_attempts={db.query(func.count(MockAttempt.id)).scalar()}")

        invalid = 0
        for a in db.query(WritingAttempt).limit(500).all():
            for band in (a.estimated_band, a.teacher_band, a.final_band):
                if band is not None and sanitize_band(band) is None:
                    invalid += 1
        print(f"writing_invalid_bands={invalid}")

        orphan_reading = (
            db.query(func.count(ReadingAttempt.id))
            .outerjoin(User, User.id == ReadingAttempt.student_id)
            .filter(User.id.is_(None))
            .scalar()
        )
        print(f"orphan_reading_attempts={orphan_reading}")
        print("=== end diagnose ===")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
