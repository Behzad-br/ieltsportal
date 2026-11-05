"""Read-only database integrity helpers (Module 19).

Used by diagnostics and tests. Never mutates rows.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.listening.models import ListeningAttempt
from app.reading.models import ReadingAttempt
from app.speaking.models import TestSession
from app.writing.models import WritingAttempt, WritingMockSession


EXPECTED_TABLES = frozenset(
    {
        "auth_branches",
        "auth_batches",
        "auth_users",
        "reading_attempts",
        "listening_attempts",
        "writing_questions",
        "writing_generations",
        "writing_mock_sessions",
        "writing_attempts",
        "writing_attempt_revisions",
        "writing_bookmarks",
        "writing_assignments",
        "writing_model_answers",
        "writing_ai_usage",
        "writing_app_settings",
        "test_sessions",
        "questions",
        "answers",
        "evaluations",
        "homework_assignments",
        "homework_submissions",
        "class_notes",
        "mock_library_items",
        "mock_assignments",
        "mock_assignment_students",
        "mock_attempts",
        "mock_answers",
        "mock_integrity_events",
        "teacher_mock_actions",
        "lms_notifications",
    }
)

MIGRATION_HEAD = "20261012_0007"


def resolve_skill_owner(db: Session, skill: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Return (owner_student_id, issue) for a mock/homework skill_ref dict.

    issue is None when the linked row exists (owner may still be None for legacy).
    """
    if not isinstance(skill, dict) or not skill:
        return None, "empty"
    kind = (skill.get("kind") or "").strip().lower()
    if kind == "reading":
        rid = skill.get("attemptId") or skill.get("ref")
        if not rid:
            return None, "missing_ref"
        row = db.query(ReadingAttempt).filter_by(id=rid).first()
        if not row:
            return None, "missing_row"
        return row.student_id, None
    if kind == "listening":
        rid = skill.get("attemptId") or skill.get("ref")
        if not rid:
            return None, "missing_ref"
        row = db.query(ListeningAttempt).filter_by(id=rid).first()
        if not row:
            return None, "missing_row"
        return row.student_id, None
    if kind == "writing-question":
        rid = skill.get("ref") or skill.get("attemptId")
        if not rid:
            return None, "missing_ref"
        row = db.query(WritingAttempt).filter_by(id=rid).first()
        if not row:
            return None, "missing_row"
        return row.student_id, None
    if kind == "writing-mock":
        rid = skill.get("mockId") or skill.get("ref")
        if not rid:
            return None, "missing_ref"
        row = db.query(WritingMockSession).filter_by(id=rid).first()
        if not row:
            return None, "missing_row"
        return row.student_id, None
    if kind == "speaking":
        rid = skill.get("sessionId") or skill.get("ref")
        if not rid:
            return None, "missing_ref"
        row = db.query(TestSession).filter_by(id=rid).first()
        if not row:
            return None, "missing_row"
        return row.student_id, None
    if kind == "full":
        return None, "full_deferred"
    return None, f"unknown_kind:{kind}"


def skill_ref_cross_user(db: Session, student_id: str, skill: dict[str, Any]) -> bool:
    """True when linked skill attempt exists but belongs to another student."""
    owner, issue = resolve_skill_owner(db, skill)
    if issue:
        return False
    return bool(owner and student_id and owner != student_id)


def scan_failure_states(db: Session, *, limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    """Read-only diagnostics for stuck/inconsistent academic rows (Module 22).

    Never mutates. Callers decide recovery via existing retry routes.
    """
    from app.homework.models import HomeworkSubmission
    from app.mocks.models import MockAttempt
    from app.notes.models import ClassNote
    from app.speaking.models import Answer
    from pathlib import Path
    from app.config import settings

    out: dict[str, list[dict[str, Any]]] = {
        "writing_grading_stuck": [],
        "reading_submitted_no_result": [],
        "listening_submitted_no_result": [],
        "homework_in_progress_missing_skill": [],
        "mock_in_progress_missing_skill": [],
        "speaking_transcription_error": [],
        "notes_missing_file": [],
    }

    for row in (
        db.query(WritingAttempt)
        .filter(WritingAttempt.status == "grading")
        .limit(limit)
        .all()
    ):
        out["writing_grading_stuck"].append(
            {
                "id": row.id,
                "student_id": row.student_id,
                "grading_error": bool(row.grading_error),
            }
        )

    for row in (
        db.query(ReadingAttempt)
        .filter(ReadingAttempt.status == "submitted")
        .limit(limit)
        .all()
    ):
        if getattr(row, "result", None) is None and getattr(row, "estimated_band", None) is None:
            out["reading_submitted_no_result"].append({"id": row.id, "student_id": row.student_id})

    for row in (
        db.query(ListeningAttempt)
        .filter(ListeningAttempt.status == "submitted")
        .limit(limit)
        .all()
    ):
        if getattr(row, "result", None) is None and getattr(row, "estimated_band", None) is None:
            out["listening_submitted_no_result"].append({"id": row.id, "student_id": row.student_id})

    for row in (
        db.query(HomeworkSubmission)
        .filter(HomeworkSubmission.status == "in_progress")
        .limit(limit)
        .all()
    ):
        if not row.attempt_ref and not row.attempt_kind:
            out["homework_in_progress_missing_skill"].append(
                {"id": row.id, "assignment_id": row.assignment_id, "student_id": row.student_id}
            )

    for row in (
        db.query(MockAttempt).filter(MockAttempt.status == "in_progress").limit(limit).all()
    ):
        skill = row.skill_ref if isinstance(row.skill_ref, dict) else None
        if not skill:
            out["mock_in_progress_missing_skill"].append(
                {"id": row.id, "student_id": row.student_id, "assignment_id": row.assignment_id}
            )
        else:
            _, issue = resolve_skill_owner(db, skill)
            if issue and issue not in {"full_deferred"}:
                out["mock_in_progress_missing_skill"].append(
                    {
                        "id": row.id,
                        "student_id": row.student_id,
                        "issue": issue,
                    }
                )

    for row in (
        db.query(Answer)
        .filter(Answer.transcription_error.isnot(None))
        .limit(limit)
        .all()
    ):
        out["speaking_transcription_error"].append(
            {"id": row.id, "session_id": row.session_id, "has_audio": bool(row.audio_path)}
        )

    notes_dir = Path(settings.NOTES_DIR)
    for row in db.query(ClassNote).limit(limit).all():
        stored = (row.stored_name or "").strip()
        if not stored:
            out["notes_missing_file"].append({"id": row.id, "reason": "empty_stored_name"})
            continue
        path = notes_dir / stored
        if not path.exists():
            out["notes_missing_file"].append({"id": row.id, "stored_name": stored})

    return out
