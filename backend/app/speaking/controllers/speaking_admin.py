"""Teacher/admin Speaking review — JWT staff, branch-scoped."""
from __future__ import annotations

from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.access import users_in_branch
from app.auth.deps import get_optional_user
from app.auth.models import User
from app.database import get_db
from app.speaking.models import Answer, Evaluation, TestSession
from app.speaking.services.audio_store import resolve_recording
from app.speaking.views import evaluation_to_schema

router = APIRouter()


def require_staff(user: Optional[User] = Depends(get_optional_user)) -> User:
    if not user or user.role not in {"teacher", "branch_admin", "super_admin"}:
        raise HTTPException(403, "Staff authentication required.")
    return user


def _allowed_student_ids(db: Session, actor: User) -> set[str] | None:
    if actor.role == "super_admin":
        return None
    return set(users_in_branch(db, actor.branch_id or "", role="student"))


def _assert_session_in_scope(db: Session, session: TestSession, actor: User) -> None:
    allowed = _allowed_student_ids(db, actor)
    if allowed is None:
        return
    if (session.student_id or "") not in allowed:
        raise HTTPException(403, "Not allowed for this speaking session.")


@router.get("/speaking/admin/sessions")
async def list_sessions(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_staff),
):
    q = db.query(TestSession).filter(TestSession.student_id.isnot(None))
    if status:
        q = q.filter(TestSession.status == status)
    rows = q.order_by(TestSession.started_at.desc()).limit(100).all()
    allowed = _allowed_student_ids(db, actor)
    if allowed is not None:
        rows = [r for r in rows if (r.student_id or "") in allowed]
    return {
        "sessions": [
            {
                "id": r.id,
                "student_id": r.student_id,
                "title": r.title,
                "status": r.status,
                "estimated_band": r.estimated_band,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ]
    }


@router.get("/speaking/admin/sessions/{session_id}")
async def session_detail(
    session_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_staff),
):
    session = db.query(TestSession).filter_by(id=session_id).first()
    if not session:
        raise HTTPException(404, "Session not found.")
    _assert_session_in_scope(db, session, actor)
    answers = db.query(Answer).filter_by(session_id=session_id).all()
    evaluation = db.query(Evaluation).filter_by(session_id=session_id).first()
    band = None
    source = None
    if evaluation:
        if evaluation.teacher_band is not None:
            band = evaluation.teacher_band
            source = "teacher"
        else:
            band = evaluation.final_band if evaluation.final_band is not None else evaluation.estimated_band
            source = "ai"
    return {
        "id": session.id,
        "student_id": session.student_id,
        "title": session.title,
        "status": session.status,
        "estimated_band": session.estimated_band,
        "final_band": band,
        "band_source": source,
        "evaluation": evaluation_to_schema(evaluation) if evaluation else None,
        "teacher_band": evaluation.teacher_band if evaluation else None,
        "teacher_comments": evaluation.teacher_comments if evaluation else None,
        "answers": [
            {
                "id": a.id,
                "question_id": a.question_id,
                "transcript": a.transcript,
                "audio_duration": a.audio_duration,
                "has_audio": bool(a.audio_path),
                "transcription_error": a.transcription_error,
            }
            for a in answers
        ],
    }


@router.get("/speaking/admin/answers/{answer_id}/audio")
async def admin_answer_audio(
    answer_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_staff),
):
    answer = db.query(Answer).filter_by(id=answer_id).first()
    if not answer:
        raise HTTPException(404, "Recording not found.")
    session = db.query(TestSession).filter_by(id=answer.session_id).first()
    if not session:
        raise HTTPException(404, "Session not found.")
    _assert_session_in_scope(db, session, actor)
    path = resolve_recording(answer.audio_path)
    if not path:
        raise HTTPException(404, "Recording file missing.")
    return FileResponse(
        path,
        media_type=answer.audio_mime or "audio/webm",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
    )


@router.post("/speaking/admin/sessions/{session_id}/review")
async def teacher_review(
    session_id: str,
    body: dict,
    db: Session = Depends(get_db),
    actor: User = Depends(require_staff),
):
    session = db.query(TestSession).filter_by(id=session_id).first()
    if not session:
        raise HTTPException(404, "Session not found.")
    _assert_session_in_scope(db, session, actor)
    evaluation = db.query(Evaluation).filter_by(session_id=session_id).first()
    if not evaluation:
        # Allow teacher review even if AI never ran — create shell evaluation.
        evaluation = Evaluation(session_id=session_id)
        db.add(evaluation)
    if "teacher_band" in body:
        from app.ielts_bands import parse_ielts_band

        evaluation.teacher_band = parse_ielts_band(body["teacher_band"], field="teacher_band")
    if "teacher_comments" in body:
        evaluation.teacher_comments = body.get("teacher_comments")
    if evaluation.teacher_band is not None:
        evaluation.final_band = evaluation.teacher_band
        session.estimated_band = session.estimated_band  # keep AI estimate on session
    elif evaluation.estimated_band is not None:
        evaluation.final_band = evaluation.estimated_band
    db.commit()
    return {
        "id": session.id,
        "teacher_band": evaluation.teacher_band,
        "estimated_band": evaluation.estimated_band,
        "final_band": evaluation.final_band,
        "teacher_comments": evaluation.teacher_comments,
    }
