"""Read-only Neon diagnostics for Module 16 Results integrity (no mutations)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.homework.models import HomeworkSubmission
from app.ielts_bands import is_valid_ielts_band
from app.listening.models import ListeningAttempt
from app.mocks.models import MockAttempt
from app.reading.models import ReadingAttempt
from app.speaking.models import Evaluation, TestSession
from app.writing.models import WritingAttempt


def _band_issues(label: str, rows, getters) -> list[str]:
    out = []
    for row in rows:
        for name, getter in getters:
            val = getter(row)
            if val is not None and not is_valid_ielts_band(val):
                out.append(f"{label} id={getattr(row, 'id', '?')} {name}={val}")
    return out


def main() -> int:
    db = SessionLocal()
    try:
        print("=== M16 Results diagnostics (read-only) ===")
        reading = db.query(ReadingAttempt).filter(ReadingAttempt.status == "submitted").count()
        listening = db.query(ListeningAttempt).filter(ListeningAttempt.status == "submitted").count()
        writing = db.query(WritingAttempt).count()
        speaking = db.query(TestSession).filter(TestSession.status == "completed").count()
        mocks = db.query(MockAttempt).count()
        hw = db.query(HomeworkSubmission).count()
        print(f"  submitted_reading={reading} submitted_listening={listening}")
        print(f"  writing_attempts={writing} speaking_completed={speaking}")
        print(f"  mock_attempts={mocks} homework_submissions={hw}")

        issues = []
        issues += _band_issues(
            "writing",
            db.query(WritingAttempt).limit(500).all(),
            [
                ("estimated", lambda r: r.estimated_band),
                ("teacher", lambda r: r.teacher_band),
                ("final", lambda r: r.final_band),
            ],
        )
        issues += _band_issues(
            "reading",
            db.query(ReadingAttempt).limit(500).all(),
            [("estimated", lambda r: r.estimated_band)],
        )
        issues += _band_issues(
            "listening",
            db.query(ListeningAttempt).limit(500).all(),
            [("estimated", lambda r: r.estimated_band)],
        )
        issues += _band_issues(
            "speaking_eval",
            db.query(Evaluation).limit(500).all(),
            [
                ("estimated", lambda r: r.estimated_band),
                ("teacher", lambda r: r.teacher_band),
                ("final", lambda r: r.final_band),
            ],
        )
        issues += _band_issues(
            "mock",
            db.query(MockAttempt).limit(500).all(),
            [
                ("listening", lambda r: r.listening_band),
                ("reading", lambda r: r.reading_band),
                ("writing", lambda r: r.writing_band),
                ("speaking", lambda r: r.speaking_band),
                ("overall", lambda r: r.overall_band),
            ],
        )

        missing_student = db.query(MockAttempt).filter(
            (MockAttempt.student_id == None) | (MockAttempt.student_id == "")  # noqa: E711
        ).count()
        print(f"  mock_attempts_missing_student={missing_student}")
        print(f"  invalid_band_samples={len(issues)}")
        for line in issues[:20]:
            print(f"    {line}")
        if len(issues) > 20:
            print(f"    ... and {len(issues) - 20} more")
        print("=== end diagnostics ===")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
