"""Teacher homework assign + student start/submit across four IELTS skills."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.access import student_batch_from_db, users_in_branch
from app.auth.deps import get_optional_user, resolve_student_id as get_student_id
from app.auth.models import User
from app.config import settings
from app.database import get_db
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.listening.models import ListeningAttempt
from app.listening.services import bank as listening_bank
from app.listening.services.generate import generate_ai_listening
from app.reading.models import ReadingAttempt
from app.reading.services import bank as reading_bank
from app.reading.services.generate import generate_ai_test
from app.speaking.services import question_bank, speaking_session
from app.speaking.services import question_generator
from app.writing.models import WritingQuestion
from app.writing.services import writing_ai, writing_service

router = APIRouter()


def _resolve_manual_writing_image(payload: dict) -> Optional[str]:
    raw = payload.get("image_path") or payload.get("image_url") or ""
    if not raw:
        return None
    text = str(raw)
    if text.startswith("/api/mocks/images/"):
        name = Path(text).name
        candidate = Path(settings.MOCK_IMAGE_DIR) / name
        return str(candidate) if candidate.is_file() else None
    path = Path(text)
    return str(path) if path.is_file() else None


def require_teacher(
    user: User | None = Depends(get_optional_user),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> str:
    if user and user.role in {"teacher", "branch_admin", "super_admin"}:
        return user.id
    # Ops shared token — disabled in production unless explicitly opted in.
    if settings.writing_admin_token_allowed:
        expected = (settings.WRITING_ADMIN_TOKEN or "").strip()
        provided = (x_admin_token or "").strip()
        if expected and provided == expected:
            return "teacher"
    if settings.AUTH_LEGACY_HEADERS and not (settings.WRITING_ADMIN_TOKEN or "").strip():
        return "teacher"
    raise HTTPException(403, "Teacher authentication required.")


_STUDENT_PAYLOAD_SECRET_KEYS = frozenset(
    {
        "answer",
        "answer_key",
        "answer_text",
        "accepted_answers",
        "answers",
        "evidence",
        "source_locator",
        "explanation",
        "correct",
        "is_correct",
        "correct_answer",
        "model_answer",
    }
)


def _student_safe_payload(value):
    """Recursively strip scoring keys from homework payload before student GET."""
    if isinstance(value, dict):
        return {
            k: _student_safe_payload(v)
            for k, v in value.items()
            if k not in _STUDENT_PAYLOAD_SECRET_KEYS
        }
    if isinstance(value, list):
        return [_student_safe_payload(v) for v in value]
    return value


def _staff_user(
    user: User | None = Depends(get_optional_user),
    _: str = Depends(require_teacher),
) -> User | None:
    """JWT staff actor when present; None for shared-token / legacy teacher key."""
    if user and user.role in {"teacher", "branch_admin", "super_admin"}:
        return user
    return None


def _homework_touches_branch(db: Session, row: HomeworkAssignment, branch_id: str) -> bool:
    if not branch_id:
        return False
    branch_students = set(users_in_branch(db, branch_id, role="student"))
    if branch_students and set(row.student_ids) & branch_students:
        return True
    if not row.student_ids and row.batch_label:
        return (
            db.query(User)
            .filter_by(role="student", branch_id=branch_id, batch=row.batch_label, is_active=True)
            .first()
            is not None
        )
    return False


def _homework_visible_to_staff(db: Session, row: HomeworkAssignment, actor: User | None) -> bool:
    """Teacher: own rows (+ legacy rows that touch own branch). BA: branch. SA/token: all."""
    if actor is None or actor.role == "super_admin":
        return True
    if actor.role == "teacher":
        if (row.created_by or "") == actor.id:
            return True
        # Legacy created_by="teacher"/empty: only if assignment touches this teacher's branch.
        if (row.created_by or "") in {"teacher", ""}:
            return _homework_touches_branch(db, row, actor.branch_id or "")
        return False
    if actor.role == "branch_admin":
        if (row.created_by or "") == actor.id:
            return True
        return _homework_touches_branch(db, row, actor.branch_id or "")
    return False


def _require_homework_access(db: Session, row: HomeworkAssignment, actor: User | None) -> None:
    if not _homework_visible_to_staff(db, row, actor):
        raise HTTPException(403, "Not allowed for this assignment.")


def _row_public(row: HomeworkAssignment, extra: Optional[dict] = None, *, for_student: bool = False) -> dict:
    payload = row.payload
    data = {
        "id": row.id,
        "title": row.title,
        "module": row.module,
        "scope": row.scope,
        "source": row.source,
        "task_label": row.task_label,
        "question_type": row.question_type,
        "batch_label": row.batch_label,
        "deadline": row.deadline.isoformat() if row.deadline else None,
        "ai_grading_enabled": row.ai_grading_enabled == "1",
        "preview": payload.get("preview") or payload.get("prompt") or row.title,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    # Never expose peer student UUID lists to students.
    if not for_student:
        data["student_ids"] = row.student_ids
    if extra:
        data.update(extra)
    return data


def _deadline_passed(row: HomeworkAssignment) -> bool:
    """Server-clock late check. Naive and aware deadlines both supported."""
    if not row.deadline:
        return False
    dl = row.deadline
    now = datetime.utcnow()
    if getattr(dl, "tzinfo", None) is not None:
        dl = dl.replace(tzinfo=None)
    return now > dl


def _visible_to(db: Session, row: HomeworkAssignment, student_id: str, student_batch: str = "") -> bool:
    ids = row.student_ids
    if student_id in ids:
        return True
    if not ids and row.batch_label:
        # Entire-batch: DB batch match AND same campus as assignment creator.
        if not student_batch or student_batch.strip().lower() != (row.batch_label or "").strip().lower():
            return False
        student = db.query(User).filter_by(id=student_id).first()
        student_branch = (student.branch_id or "").strip() if student else ""
        creator = db.query(User).filter_by(id=row.created_by).first() if row.created_by else None
        if creator and (creator.branch_id or "").strip():
            return (creator.branch_id or "").strip() == student_branch and bool(student_branch)
        # Legacy token-created rows without a real creator user.
        if settings.AUTH_LEGACY_HEADERS:
            return True
        return False
    return student_id == settings.DEFAULT_STUDENT_ID and (
        not ids or "local" in ids or settings.DEFAULT_STUDENT_ID in ids
    )


def _reuse_open_session(db: Session, sub: HomeworkSubmission) -> Optional[dict]:
    """Return existing in-progress skill session payload without spawning a duplicate."""
    ref = sub.attempt_ref
    kind = sub.attempt_kind
    if not ref or not kind:
        return None
    if kind == "writing-question":
        from app.writing.models import WritingAttempt

        a = db.query(WritingAttempt).filter_by(id=ref).first()
        if a and a.status == "in_progress" and a.student_id == sub.student_id:
            return {"kind": kind, "questionId": a.question_id, "ref": ref}
        return None
    if kind == "writing-mock":
        from app.writing.models import WritingMockSession

        m = db.query(WritingMockSession).filter_by(id=ref).first()
        if m and m.status == "in_progress" and m.student_id == sub.student_id:
            return {"kind": kind, "mockId": ref, "ref": ref}
        return None
    if kind == "reading":
        a = db.query(ReadingAttempt).filter_by(id=ref).first()
        if a and a.status == "in_progress" and a.student_id == sub.student_id:
            return {"kind": kind, "attemptId": ref, "ref": ref}
        return None
    if kind == "listening":
        a = db.query(ListeningAttempt).filter_by(id=ref).first()
        if a and a.status == "in_progress" and a.student_id == sub.student_id:
            return {"kind": kind, "attemptId": ref, "ref": ref}
        return None
    if kind == "speaking":
        from app.speaking.models import TestSession

        s = db.query(TestSession).filter_by(id=ref).first()
        if s and s.status == "in_progress" and s.student_id == sub.student_id:
            return {"kind": kind, "sessionId": ref, "ref": ref}
        return None
    return None


def _band_from_linked_attempt(db: Session, sub: HomeworkSubmission) -> Optional[float]:
    """Server-stored skill band only — never trust client bands; require ownership."""
    ref = sub.attempt_ref
    kind = sub.attempt_kind or ""
    owner = sub.student_id
    if not ref or not owner:
        return None
    try:
        if kind.startswith("writing"):
            from app.writing.models import WritingAttempt, WritingMockSession

            if kind == "writing-mock":
                m = db.query(WritingMockSession).filter_by(id=ref).first()
                if not m or m.student_id != owner:
                    return None
                return float(m.overall_band) if m.overall_band is not None else None
            a = db.query(WritingAttempt).filter_by(id=ref).first()
            if not a or a.student_id != owner:
                return None
            if a.final_band is not None:
                return float(a.final_band)
            if a.estimated_band is not None:
                return float(a.estimated_band)
            return None
        if kind == "reading":
            a = db.query(ReadingAttempt).filter_by(id=ref).first()
            if not a or a.student_id != owner:
                return None
            return float(a.estimated_band) if a.estimated_band is not None else None
        if kind == "listening":
            a = db.query(ListeningAttempt).filter_by(id=ref).first()
            if not a or a.student_id != owner:
                return None
            return float(a.estimated_band) if a.estimated_band is not None else None
        if kind == "speaking":
            from app.speaking.models import TestSession

            s = db.query(TestSession).filter_by(id=ref).first()
            if not s or s.student_id != owner:
                return None
            return float(s.estimated_band) if s.estimated_band is not None else None
    except (TypeError, ValueError):
        return None
    return None


@router.get("/homework/teacher/bank")
async def teacher_bank(module: str, task: str = "", db: Session = Depends(get_db), _: str = Depends(require_teacher)):
    module = (module or "").lower()
    task = task or ""
    items = []
    if module == "writing":
        q = db.query(WritingQuestion).filter(WritingQuestion.status == "published")
        if "general" in task.lower():
            q = q.filter(WritingQuestion.test_type == "general_training")
        elif "academic" in task.lower() or "task 1" in task.lower() or "task 2" in task.lower():
            if "general" not in task.lower():
                q = q.filter(WritingQuestion.test_type == "academic")
        if "task 1" in task.lower() and "full" not in task.lower():
            q = q.filter(WritingQuestion.task_number == 1)
        elif "task 2" in task.lower():
            q = q.filter(WritingQuestion.task_number == 2)
        rows = q.order_by(WritingQuestion.created_at.desc()).limit(80).all()
        items = [
            {
                "id": r.id,
                "title": r.title or r.topic or "Writing task",
                "detail": (r.prompt or "")[:280],
            }
            for r in rows
        ]
    elif module == "reading":
        catalog = reading_bank.catalog()
        tests = catalog["academic"] if "general" not in task.lower() else catalog["general_training"]
        p_index = 0
        if "2" in task:
            p_index = 1
        if "3" in task:
            p_index = 2
        for t in tests:
            passage = (t.get("passages") or [None])[p_index] if t.get("passages") else None
            items.append(
                {
                    "id": t["id"],
                    "title": t.get("title") or t["id"],
                    "detail": f"{t.get('question_count') or 40} questions",
                    "passage_id": (passage or {}).get("id") if "full" not in task.lower() else None,
                }
            )
    elif module == "listening":
        catalog = listening_bank.catalog()
        part_num = 1
        for n in (4, 3, 2, 1):
            if str(n) in task:
                part_num = n
                break
        for t in catalog.get("tests") or []:
            part = next((p for p in t.get("parts") or [] if p.get("part_number") == part_num), None)
            items.append(
                {
                    "id": t["id"],
                    "title": t.get("title") or t["id"],
                    "detail": f"{t.get('question_count') or 40} questions",
                    "part_id": None if "full" in task.lower() else (part or {}).get("id"),
                }
            )
    else:
        for t in question_bank.list_tests():
            items.append({"id": t["id"], "title": t["title"], "detail": "Speaking test"})
    return {"items": items}


@router.post("/homework/teacher/preview")
async def teacher_preview(body: dict, db: Session = Depends(get_db), _: str = Depends(require_teacher)):
    module = (body.get("module") or "writing").lower()
    if module != "speaking":
        raise HTTPException(400, "AI generate is only available for Speaking.")
    scope = body.get("scope") or "piece"
    task = body.get("task") or ""
    qtype = body.get("question_type") or ""
    try:
        if module == "writing":
            test_type = "general_training" if "general" in task.lower() else "academic"
            task_number = 1 if "task 1" in task.lower() else 2
            mapped = _map_writing_type(qtype, task_number)
            payload = await writing_ai.generate_question_payload(
                db,
                test_type=test_type,
                task_number=task_number,
                question_type=mapped,
                topic=body.get("topic"),
                difficulty=None,
                student_id="teacher",
            )
            q, _gen = writing_service.persist_generation(db, "teacher", payload, parameters=body, permanent_draft=False)
            q.status = "published"
            db.commit()
            db.refresh(q)
            preview = _writing_preview(q.prompt, q.title, task_number, mapped)
            return {
                "preview": preview,
                "title": q.title or q.topic or "AI writing task",
                "payload": {"question_id": q.id, "prompt": q.prompt, "preview": preview},
            }
        if module == "reading":
            test_type = "general_training" if "general" in task.lower() else "academic"
            mapped_r = _map_reading_type(qtype) if scope != "full_mock" else None
            sliced = await generate_ai_test(
                test_type=test_type,
                full_mock=scope == "full_mock",
                question_type=mapped_r,
            )
            preview = _reading_preview(sliced)
            return {
                "preview": preview,
                "title": sliced.get("title") or "AI reading",
                "payload": {"snapshot": sliced, "preview": preview},
            }
        if module == "listening":
            part_num = 1
            for n in (4, 3, 2, 1):
                if str(n) in task:
                    part_num = n
                    break
            mapped_l = _map_listening_type(qtype) if scope != "full_mock" else None
            sliced = await generate_ai_listening(
                full_mock=scope == "full_mock",
                question_type=mapped_l,
                part_number=part_num,
            )
            preview = _listening_preview(sliced)
            return {
                "preview": preview,
                "title": sliced.get("title") or "AI listening",
                "payload": {"snapshot": sliced, "preview": preview},
            }
        focus = None
        for n in (1, 2, 3):
            if str(n) in task:
                focus = n
                break
        test_data = await question_generator.generate_fresh_test(
            recent_topics=[],
            theme=qtype if qtype and "full" not in qtype.lower() else None,
            focus_part=focus,
        )
        preview = _speaking_preview(test_data, focus if scope != "full_mock" else None)
        return {
            "preview": preview,
            "title": test_data.get("title") or "AI speaking",
            "payload": {"speaking_test": test_data, "preview": preview},
        }
    except Exception as exc:
        raise HTTPException(502, f"AI generate failed: {exc}") from exc


@router.post("/homework/teacher/upload-audio")
async def upload_audio(
    request: Request,
    file: UploadFile = File(...),
    _: str = Depends(require_teacher),
):
    from app.security_hardening import client_ip, rate_limit_or_429, upload_limiter

    rate_limit_or_429(upload_limiter, f"upload:{client_ip(request)}")
    dest_dir = Path(settings.HOMEWORK_AUDIO_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    original = Path(file.filename or "audio.mp3").name
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else "mp3"
    if ext not in {"mp3", "wav", "ogg", "m4a", "webm", "mpeg", "mp4"}:
        raise HTTPException(400, "Unsupported audio type.")
    name = f"{uuid.uuid4().hex[:10]}_{original}"
    path = dest_dir / name
    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large. Max {settings.MAX_UPLOAD_SIZE_MB}MB.")
    if not data:
        raise HTTPException(400, "Empty file.")
    path.write_bytes(data)
    return {"filename": name, "url": f"/api/homework/audio/{name}"}


def _homework_audio_allowed(db: Session, user: User, filename: str) -> bool:
    """Staff may fetch upload previews; students only if assigned homework references the file."""
    if user.role in {"teacher", "branch_admin", "super_admin"}:
        if user.role == "super_admin":
            return True
        # Branch-scoped staff: any assignment that touches their campus, or they created.
        rows = (
            db.query(HomeworkAssignment)
            .filter(HomeworkAssignment.payload_json.isnot(None))
            .filter(HomeworkAssignment.payload_json.contains(filename))
            .all()
        )
        for row in rows:
            if _homework_visible_to_staff(db, row, user):
                return True
        return False
    if user.role != "student":
        return False
    batch = student_batch_from_db(db, user.id)
    rows = (
        db.query(HomeworkAssignment)
        .filter(HomeworkAssignment.payload_json.isnot(None))
        .filter(HomeworkAssignment.payload_json.contains(filename))
        .all()
    )
    return any(_visible_to(db, row, user.id, batch) for row in rows)


@router.get("/homework/audio/{filename}")
async def homework_audio(
    filename: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    from fastapi.responses import FileResponse

    if user is None:
        if settings.AUTH_LEGACY_HEADERS:
            # Sandbox/tests only — no object ACL under legacy.
            pass
        else:
            raise HTTPException(401, "Authentication required.")
    safe = Path(filename).name
    if safe != filename or ".." in filename:
        raise HTTPException(400, "Invalid filename.")
    if user is not None and not settings.AUTH_LEGACY_HEADERS:
        if not _homework_audio_allowed(db, user, safe):
            raise HTTPException(404, "Audio not found.")
    path = Path(settings.HOMEWORK_AUDIO_DIR) / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Audio not found.")
    try:
        path.resolve().relative_to(Path(settings.HOMEWORK_AUDIO_DIR).resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename.")
    return FileResponse(path, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.post("/homework/teacher/assignments")
async def create_assignment(
    body: dict,
    db: Session = Depends(get_db),
    teacher_key: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    deadline = body.get("deadline")
    parsed = datetime.fromisoformat(deadline.replace("Z", "+00:00")) if deadline else None
    student_ids = [str(s).strip() for s in (body.get("student_ids") or []) if str(s).strip()]
    # Staging: never inject "local"/DEFAULT_STUDENT_ID into new assignments.
    # Legacy tests with AUTH_LEGACY_HEADERS may still use DEFAULT for empty entire-batch.
    if body.get("assignment_target") == "entire" or not student_ids:
        if settings.AUTH_LEGACY_HEADERS:
            student_ids = list({*student_ids, settings.DEFAULT_STUDENT_ID})
        elif not student_ids:
            # Entire-batch without explicit IDs: store empty list + batch_label (Phase 7 will normalize).
            student_ids = []
    # Reject blocked legacy ids on new writes when staging-strict.
    if not settings.AUTH_LEGACY_HEADERS:
        from app.auth.identity import is_blocked_legacy_id

        bad = [s for s in student_ids if is_blocked_legacy_id(s)]
        if bad:
            raise HTTPException(400, f"Invalid student ids for staging: {', '.join(bad)}")
    # Teachers/BA may only assign students in their branch.
    if actor and actor.role in {"teacher", "branch_admin"} and student_ids:
        allowed = set(users_in_branch(db, actor.branch_id or "", role="student"))
        foreign = [sid for sid in student_ids if sid not in allowed]
        if foreign:
            raise HTTPException(403, "Cannot assign students outside your branch.")
    # Entire-batch labels must exist on the actor's campus (no cross-branch batch names).
    batch_label = (body.get("batch_label") or "").strip()
    if actor and actor.role in {"teacher", "branch_admin"} and batch_label:
        from app.auth.models import Batch

        on_campus = (
            db.query(Batch)
            .filter_by(branch_id=actor.branch_id or "", name=batch_label)
            .first()
            is not None
        )
        has_students = (
            db.query(User)
            .filter_by(role="student", branch_id=actor.branch_id or "", batch=batch_label, is_active=True)
            .first()
            is not None
        )
        if not on_campus and not has_students:
            raise HTTPException(400, "batch_label is not on your campus.")
    row = HomeworkAssignment(
        title=body.get("title") or f"{body.get('module')} homework",
        module=(body.get("module") or "writing").lower(),
        scope=body.get("scope") or "piece",
        source=body.get("source") or "bank",
        task_label=body.get("task"),
        question_type=body.get("question_type"),
        batch_label=body.get("batch_label"),
        deadline=parsed,
        ai_grading_enabled="1" if body.get("ai_grading_enabled") else "0",
        created_by=(actor.id if actor else teacher_key),
    )
    row.student_ids = student_ids
    row.payload = body.get("payload") or {}
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_public(row)


@router.get("/homework/teacher/assignments")
async def list_teacher_assignments(
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    rows = db.query(HomeworkAssignment).order_by(HomeworkAssignment.created_at.desc()).all()
    out = []
    for row in rows:
        if not _homework_visible_to_staff(db, row, actor):
            continue
        subs = db.query(HomeworkSubmission).filter_by(assignment_id=row.id).all()
        submitted = len([s for s in subs if s.status in ("submitted", "reviewed", "published")])
        out.append(_row_public(row, extra={"submitted": submitted, "assigned": max(len(row.student_ids), 1)}))
    return {"assignments": out}


@router.delete("/homework/teacher/assignments/{assignment_id}")
async def delete_assignment(
    assignment_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    row = db.query(HomeworkAssignment).filter_by(id=assignment_id).first()
    if not row:
        raise HTTPException(404, "Assignment not found.")
    _require_homework_access(db, row, actor)
    # Preserve academic history — refuse hard delete when submissions exist.
    if db.query(HomeworkSubmission).filter_by(assignment_id=assignment_id).count():
        raise HTTPException(
            409,
            "Cannot delete homework with submissions. Archive by leaving it inactive for new starts.",
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/homework/teacher/assignments/{assignment_id}/submissions")
async def teacher_submissions(
    assignment_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    row = db.query(HomeworkAssignment).filter_by(id=assignment_id).first()
    if not row:
        raise HTTPException(404, "Assignment not found.")
    _require_homework_access(db, row, actor)
    subs = db.query(HomeworkSubmission).filter_by(assignment_id=assignment_id).all()
    return {
        "assignment": _row_public(row),
        "submissions": [
            {
                "id": s.id,
                "student_id": s.student_id,
                "status": s.status,
                "attempt_kind": s.attempt_kind,
                "attempt_ref": s.attempt_ref,
                "estimated_band": s.estimated_band,
                "teacher_band": s.teacher_band,
                "teacher_comments": s.teacher_comments,
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            }
            for s in subs
        ],
    }


@router.post("/homework/teacher/submissions/{submission_id}/review")
async def teacher_review(
    submission_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    sub = db.query(HomeworkSubmission).filter_by(id=submission_id).first()
    if not sub:
        raise HTTPException(404, "Submission not found.")
    parent = db.query(HomeworkAssignment).filter_by(id=sub.assignment_id).first()
    if not parent:
        raise HTTPException(404, "Assignment not found.")
    _require_homework_access(db, parent, actor)
    if "teacher_band" in body:
        from app.ielts_bands import parse_ielts_band

        sub.teacher_band = parse_ielts_band(body["teacher_band"], field="teacher_band")
    if "teacher_comments" in body:
        sub.teacher_comments = body["teacher_comments"]
    sub.status = "published" if body.get("publish") else "reviewed"
    sub.reviewed_at = datetime.utcnow()
    if sub.attempt_kind == "writing" and sub.attempt_ref:
        from app.writing.models import WritingAttempt

        attempt = db.query(WritingAttempt).filter_by(id=sub.attempt_ref).first()
        if attempt:
            attempt.teacher_band = sub.teacher_band
            attempt.teacher_comments = sub.teacher_comments
            if sub.teacher_band is not None:
                attempt.final_band = sub.teacher_band
            attempt.status = "published" if body.get("publish") else "teacher_review"
    db.commit()
    return {"id": sub.id, "status": sub.status, "teacher_band": sub.teacher_band}


@router.get("/homework/teacher/student-record")
async def teacher_student_record(
    ids: str = "",
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    aliases = [item.strip() for item in (ids or "").split(",") if item.strip()]
    if not aliases:
        return {"submitted": [], "in_progress": [], "pending": []}
    # Teachers/BA may only query students in their branch.
    if actor and actor.role in {"teacher", "branch_admin"}:
        allowed = set(users_in_branch(db, actor.branch_id or "", role="student"))
        aliases = [a for a in aliases if a in allowed]
        if not aliases:
            return {"submitted": [], "in_progress": [], "pending": []}
    rows = db.query(HomeworkAssignment).order_by(HomeworkAssignment.created_at.desc()).all()
    submitted: list[dict] = []
    in_progress: list[dict] = []
    pending: list[dict] = []
    for row in rows:
        if not _homework_visible_to_staff(db, row, actor):
            continue
        sub = (
            db.query(HomeworkSubmission)
            .filter(HomeworkSubmission.assignment_id == row.id, HomeworkSubmission.student_id.in_(aliases))
            .first()
        )
        assigned = any(_visible_to(db, row, sid, student_batch_from_db(db, sid)) for sid in aliases) or sub is not None
        if not assigned:
            continue
        extra = {
            "status": sub.status if sub else "pending",
            "submission_id": sub.id if sub else None,
            "estimated_band": sub.estimated_band if sub else None,
            "teacher_band": sub.teacher_band if sub else None,
            "teacher_comments": sub.teacher_comments if sub else None,
            "submitted_at": sub.submitted_at.isoformat() if sub and sub.submitted_at else None,
        }
        item = _row_public(row, extra=extra)
        if not sub or sub.status in ("pending",):
            pending.append(item)
        elif sub.status in ("submitted", "reviewed", "published"):
            submitted.append(item)
        else:
            in_progress.append(item)
    return {"submitted": submitted, "in_progress": in_progress, "pending": pending}


@router.get("/homework/student")
async def student_list(db: Session = Depends(get_db), student_id: str = Depends(get_student_id)):
    batch = student_batch_from_db(db, student_id)
    rows = db.query(HomeworkAssignment).order_by(HomeworkAssignment.created_at.desc()).all()
    items = []
    for row in rows:
        if not _visible_to(db, row, student_id, batch):
            continue
        sub = (
            db.query(HomeworkSubmission)
            .filter_by(assignment_id=row.id, student_id=student_id)
            .first()
        )
        status = "Pending"
        if sub:
            status = {
                "pending": "Pending",
                "in_progress": "In progress",
                "submitted": "Completed",
                "reviewed": "Completed",
                "published": "Completed",
            }.get(sub.status, "Pending")
        items.append(
            _row_public(
                row,
                extra={"status": status, "submission_id": sub.id if sub else None},
                for_student=True,
            )
        )
    return {"assignments": items}


@router.get("/homework/student/{assignment_id}")
async def student_detail(assignment_id: str, db: Session = Depends(get_db), student_id: str = Depends(get_student_id)):
    row = db.query(HomeworkAssignment).filter_by(id=assignment_id).first()
    if not row or not _visible_to(db, row, student_id, student_batch_from_db(db, student_id)):
        raise HTTPException(404, "Assignment not found.")
    sub = db.query(HomeworkSubmission).filter_by(assignment_id=row.id, student_id=student_id).first()
    published = bool(sub and sub.status == "published")
    show_estimate = bool(sub and sub.status in ("submitted", "reviewed", "published"))
    return _row_public(
        row,
        extra={
            "status": sub.status if sub else "pending",
            "submission_id": sub.id if sub else None,
            # Teacher score/comments only after publish — reviewed is staff-only draft.
            "teacher_band": sub.teacher_band if published else None,
            "teacher_comments": sub.teacher_comments if published else None,
            "estimated_band": sub.estimated_band if show_estimate else None,
            "result_pending": bool(sub and sub.status in ("submitted", "reviewed") and not published),
            "band_source": (
                "teacher"
                if published and sub.teacher_band is not None
                else ("estimated" if show_estimate and sub.estimated_band is not None else None)
            ),
            "payload": _student_safe_payload(row.payload),
        },
        for_student=True,
    )


@router.post("/homework/student/{assignment_id}/start")
async def student_start(assignment_id: str, db: Session = Depends(get_db), student_id: str = Depends(get_student_id)):
    row = db.query(HomeworkAssignment).filter_by(id=assignment_id).first()
    if not row or not _visible_to(db, row, student_id, student_batch_from_db(db, student_id)):
        raise HTTPException(404, "Assignment not found.")
    sub = db.query(HomeworkSubmission).filter_by(assignment_id=row.id, student_id=student_id).first()
    if sub and sub.status in ("submitted", "reviewed", "published"):
        raise HTTPException(409, "This homework is already submitted.")
    if _deadline_passed(row) and (not sub or sub.status not in ("in_progress",)):
        raise HTTPException(403, "The deadline has passed.")

    if not sub:
        sub = HomeworkSubmission(
            assignment_id=row.id,
            student_id=student_id,
            status="in_progress",
            started_at=datetime.utcnow(),
        )
        db.add(sub)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            # Concurrent start won the unique (assignment_id, student_id) race.
            sub = (
                db.query(HomeworkSubmission)
                .filter_by(assignment_id=row.id, student_id=student_id)
                .first()
            )
            if not sub:
                raise HTTPException(409, "Could not start homework; please retry.")
            if sub.status in ("submitted", "reviewed", "published"):
                raise HTTPException(409, "This homework is already submitted.")

    else:
        sub.status = "in_progress"
        sub.started_at = sub.started_at or datetime.utcnow()

    # Resume existing in-progress skill attempt — do not spawn duplicates.
    reused = _reuse_open_session(db, sub)
    if reused:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Homework submission conflict; please retry.")
        db.refresh(sub)
        return {"submission_id": sub.id, **reused}

    session = await _open_skill(db, row, student_id)
    sub.attempt_kind = session["kind"]
    sub.attempt_ref = session.get("ref")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Homework submission conflict; please retry.")
    db.refresh(sub)
    return {"submission_id": sub.id, **session}


@router.post("/homework/student/submissions/{submission_id}/complete")
async def student_complete(submission_id: str, body: dict, db: Session = Depends(get_db), student_id: str = Depends(get_student_id)):
    sub = db.query(HomeworkSubmission).filter_by(id=submission_id, student_id=student_id).first()
    if not sub:
        raise HTTPException(404, "Submission not found.")
    # Idempotent: already finalised — never leak unpublished teacher scores.
    if sub.status in ("submitted", "reviewed", "published"):
        published = sub.status == "published"
        return {
            "id": sub.id,
            "status": sub.status,
            "estimated_band": sub.estimated_band,
            "teacher_band": sub.teacher_band if published else None,
            "result_pending": not published,
        }
    parent = db.query(HomeworkAssignment).filter_by(id=sub.assignment_id).first()
    if parent and _deadline_passed(parent) and sub.status != "in_progress":
        raise HTTPException(403, "The deadline has passed.")
    # Allow completing an in-progress attempt after deadline (started on time).
    # Never accept client attempt_ref when unset — must come from /start.
    if body.get("attempt_ref"):
        if not sub.attempt_ref or body["attempt_ref"] != sub.attempt_ref:
            raise HTTPException(400, "attempt_ref does not match this homework submission.")
    # Ignore client estimated_band entirely — scores come from linked skill only.
    server_band = _band_from_linked_attempt(db, sub)
    if server_band is not None:
        sub.estimated_band = server_band
    sub.status = "submitted"
    sub.submitted_at = datetime.utcnow()
    db.commit()
    return {"id": sub.id, "status": sub.status, "estimated_band": sub.estimated_band}


def _map_writing_type(label: str, task_number: int) -> Optional[str]:
    text = (label or "").lower()
    mapping = {
        "bar chart": "bar_chart",
        "line graph": "line_graph",
        "pie chart": "pie_chart",
        "table": "table",
        "process": "process",
        "map": "map",
        "formal": "formal_letter",
        "semi-formal": "semi_formal_letter",
        "informal": "informal_letter",
        "opinion": "opinion",
        "discussion": "discussion",
        "problem": "problem_solution",
        "advantage": "advantages_disadvantages",
        "two-part": "two_part",
    }
    for key, value in mapping.items():
        if key in text:
            return value
    return "opinion" if task_number == 2 else "bar_chart"


def _map_reading_type(label: str) -> Optional[str]:
    text = (label or "").lower()
    mapping = [
        ("true/false", "true_false_not_given"),
        ("yes/no", "yes_no_not_given"),
        ("matching headings", "matching_headings"),
        ("matching information", "matching_information"),
        ("matching features", "matching_features"),
        ("matching sentence", "matching_sentence_endings"),
        ("sentence/summary", "sentence_completion"),
        ("diagram", "sentence_completion"),
        ("short-answer", "short_answer"),
        ("short answer", "short_answer"),
        ("multiple choice", "multiple_choice"),
    ]
    for key, value in mapping:
        if key in text:
            return value
    return None


def _map_listening_type(label: str) -> Optional[str]:
    text = (label or "").lower()
    mapping = [
        ("multiple choice", "multiple_choice_single"),
        ("matching", "matching"),
        ("map", "matching"),
        ("form", "form_completion"),
        ("note", "note_completion"),
        ("table", "form_completion"),
        ("sentence", "sentence_completion"),
        ("summary", "sentence_completion"),
        ("short-answer", "short_answer"),
        ("short answer", "short_answer"),
    ]
    for key, value in mapping:
        if key in text:
            return value
    return None


def _writing_preview(prompt: str, title: str, task_number: int, qtype: Optional[str]) -> str:
    bits = [f"Writing Task {task_number}" + (f" · {qtype.replace('_', ' ')}" if qtype else "")]
    if title:
        bits.append(title)
    bits.append((prompt or "").strip())
    return "\n\n".join(b for b in bits if b)


def _reading_preview(snapshot: dict) -> str:
    lines = [snapshot.get("title") or "AI Reading"]
    for p in (snapshot.get("passages") or [])[:3]:
        paras = p.get("paragraphs") or []
        text = " ".join((x.get("text") or "") for x in paras[:2])
        lines.append(f"Passage: {p.get('title') or 'Untitled'}")
        if text:
            lines.append(text[:420] + ("…" if len(text) > 420 else ""))
    questions = snapshot.get("questions") or []
    if questions:
        lines.append("Questions:")
        for q in questions[:8]:
            lines.append(f"{q.get('number') or ''}. {q.get('prompt') or q.get('type')}")
        extra = len(questions) - min(8, len(questions))
        if extra > 0:
            lines.append(f"…and {extra} more questions")
    return "\n".join(str(x) for x in lines if x)


def _listening_preview(snapshot: dict) -> str:
    lines = [snapshot.get("title") or "AI Listening"]
    for part in snapshot.get("parts") or []:
        lines.append(f"Section {part.get('part_number')}: {part.get('title') or ''}")
        if part.get("context"):
            lines.append(part["context"])
    questions = snapshot.get("questions") or []
    if questions:
        lines.append("Questions:")
        for q in questions[:8]:
            lines.append(f"{q.get('number') or ''}. {q.get('prompt') or q.get('type')}")
        extra = len(questions) - min(8, len(questions))
        if extra > 0:
            lines.append(f"…and {extra} more questions")
    return "\n".join(str(x).strip() for x in lines if str(x).strip())


def _speaking_preview(test: dict, focus: Optional[int]) -> str:
    lines = [test.get("title") or "AI Speaking"]
    if focus in (None, 1):
        lines.append("Part 1")
        for i, q in enumerate(test.get("part1") or [], start=1):
            lines.append(f"{i}. {q}")
    if focus in (None, 2):
        p2 = test.get("part2") or {}
        lines.append("Part 2")
        lines.append(p2.get("topic") or "")
        for b in p2.get("bullets") or []:
            lines.append(f"• {b}")
    if focus in (None, 3):
        lines.append("Part 3")
        for i, q in enumerate(test.get("part3") or [], start=1):
            lines.append(f"{i}. {q}")
    return "\n".join(str(x).strip() for x in lines if str(x).strip())


async def _open_skill(db: Session, row: HomeworkAssignment, student_id: str) -> dict:
    payload = row.payload
    module = row.module
    if module == "writing":
        if row.scope == "full_mock":
            test_type = str(payload.get("test_type") or "")
            if test_type not in {"academic", "general_training"}:
                test_type = "general_training" if "general" in (row.task_label or "").lower() else "academic"
            mock = writing_service.create_mock(
                db,
                student_id,
                test_type,
                task1_id=payload.get("task1_id"),
                task2_id=payload.get("task2_id"),
                commit=False,
            )
            return {"kind": "writing-mock", "mockId": mock.id, "ref": mock.id}
        qid = payload.get("question_id")
        if not qid and row.source == "manual" and payload.get("prompt"):
            q = WritingQuestion(
                public_id=f"HW-{uuid.uuid4().hex[:10].upper()}",
                test_type=str(payload.get("test_type") or ("general_training" if "general" in (row.task_label or "").lower() else "academic")),
                task_number=int(payload.get("task_number") or (1 if "task 1" in (row.task_label or "").lower() else 2)),
                question_type=payload.get("question_type") or "opinion",
                title=row.title,
                prompt=payload["prompt"],
                status="published",
                source_type="teacher_created",
                minimum_words=150 if int(payload.get("task_number") or 2) == 1 or "task 1" in (row.task_label or "").lower() else 250,
                recommended_minutes=20 if int(payload.get("task_number") or 2) == 1 or "task 1" in (row.task_label or "").lower() else 40,
                image_path=_resolve_manual_writing_image(payload),
            )
            db.add(q)
            db.flush()
            qid = q.id
        if not qid:
            raise HTTPException(400, "No writing question on this assignment.")
        q = db.query(WritingQuestion).filter_by(id=qid).first()
        attempt = writing_service.start_attempt(db, student_id, q, commit=False)
        return {"kind": "writing-question", "questionId": qid, "ref": attempt.id}

    if module == "reading":
        snapshot = payload.get("snapshot")
        if not snapshot and (payload.get("passage") or payload.get("paragraphs") or payload.get("passages")):
            snapshot = _manual_reading_snapshot(row, payload)
        if not snapshot:
            test_id = payload.get("test_id")
            if not test_id:
                raise HTTPException(400, "No reading test on this assignment.")
            mode = "full_mock" if row.scope == "full_mock" else "single_passage"
            test = reading_bank.get_test(test_id)
            if not test:
                raise HTTPException(404, "Reading test not found.")
            if mode == "single_passage":
                snapshot = reading_bank.slice_test(test, passage_id=payload.get("passage_id"))
            else:
                snapshot = test
        minutes = snapshot.get("duration_minutes") or (60 if row.scope == "full_mock" else 20)
        attempt = ReadingAttempt(
            student_id=student_id,
            test_id=snapshot.get("id") or "homework",
            test_type=snapshot.get("test_type") or "academic",
            mode="full_mock" if row.scope == "full_mock" else "single_passage",
            timed=1 if row.timed != "0" else 0,
            duration_seconds=int(minutes) * 60,
            remaining_seconds=int(minutes) * 60,
            status="in_progress",
        )
        attempt.snapshot = snapshot
        attempt.responses = {}
        db.add(attempt)
        db.flush()
        return {"kind": "reading", "attemptId": attempt.id, "ref": attempt.id}

    if module == "listening":
        snapshot = payload.get("snapshot")
        if not snapshot and payload.get("questions"):
            snapshot = _manual_listening_snapshot(row, payload)
        if not snapshot:
            test_id = payload.get("test_id")
            test = listening_bank.get_test(test_id) if test_id else None
            if not test:
                raise HTTPException(404, "Listening test not found.")
            if row.scope == "full_mock":
                snapshot = test
            else:
                snapshot = listening_bank.slice_test(test, part_id=payload.get("part_id"))
        if payload.get("audio_filename"):
            for part in snapshot.get("parts") or []:
                part["audio_file"] = payload["audio_filename"]
        minutes = snapshot.get("duration_minutes") or 30
        attempt = ListeningAttempt(
            student_id=student_id,
            test_id=snapshot.get("id") or "homework",
            mode="full_mock" if row.scope == "full_mock" else "single_part",
            timed=1,
            duration_seconds=int(minutes) * 60,
            remaining_seconds=int(minutes) * 60,
            status="in_progress",
        )
        attempt.snapshot = snapshot
        attempt.responses = {}
        db.add(attempt)
        db.flush()
        return {"kind": "listening", "attemptId": attempt.id, "ref": attempt.id}

    practice_part = None
    if row.scope != "full_mock":
        for n in (1, 2, 3):
            if str(n) in (row.task_label or ""):
                practice_part = n
    if payload.get("speaking_test"):
        test_data = payload["speaking_test"]
        mode = "fresh"
    elif payload.get("test_id"):
        test_data = question_bank.get_test(payload["test_id"])
        mode = "stored"
        if not test_data:
            raise HTTPException(404, "Speaking test not found.")
    else:
        test_data = question_bank.get_random_test()
        mode = "stored"
    if row.source == "manual" and payload.get("prompt"):
        test_data = {
            "id": f"manual-{row.id}",
            "title": row.title,
            "part1": [payload["prompt"]] if practice_part != 2 else [],
            "part2": {"topic": payload["prompt"], "bullets": payload.get("bullets") or ["Explain your idea", "Give an example", "Say how you felt"]},
            "part3": [payload["prompt"]] if practice_part == 3 else ["Why is this important?"],
        }
        mode = "stored"
    session, _qs = speaking_session.create_session(
        db,
        mode,
        test_data,
        practice_part=practice_part,
        student_id=student_id,
        commit=False,
    )
    return {"kind": "speaking", "sessionId": session.id, "ref": session.id}


def _map_manual_reading_qtype(label: str) -> str:
    t = (label or "").strip().lower()
    if "yes" in t and "no" in t:
        return "yes_no_not_given"
    if "true" in t or "t/f" in t or "tfng" in t:
        return "true_false_not_given"
    if "heading" in t:
        return "matching_headings"
    if "matching information" in t or "which paragraph" in t:
        return "matching_information"
    if "feature" in t:
        return "matching_features"
    if "sentence ending" in t:
        return "matching_sentence_endings"
    if "diagram" in t:
        return "diagram_label_completion"
    if "summary" in t or "note completion" in t or "table" in t or "flow" in t:
        return "summary_completion"
    if "multiple" in t or "mcq" in t:
        return "multiple_choice"
    if "sentence completion" in t or "fill" in t or "blank" in t:
        return "sentence_completion"
    if "short" in t:
        return "short_answer"
    return "short_answer"


def _paragraphs_from_payload(payload: dict) -> list[dict]:
    paragraphs = payload.get("paragraphs")
    if isinstance(paragraphs, list) and paragraphs:
        out = []
        for i, para in enumerate(paragraphs):
            if isinstance(para, str):
                text = para.strip()
                if not text:
                    continue
                out.append({"label": chr(65 + i) if len(paragraphs) > 1 else None, "text": text})
                continue
            if not isinstance(para, dict):
                continue
            text = str(para.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "label": para.get("label") or (chr(65 + i) if len(paragraphs) > 1 else None),
                    "heading": para.get("heading") or None,
                    "text": text,
                }
            )
        if out:
            return out
    text = str(payload.get("passage") or "").strip()
    if not text:
        return [{"text": ""}]
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    if len(chunks) <= 1:
        return [{"text": text}]
    return [{"label": chr(65 + i), "text": chunk} for i, chunk in enumerate(chunks)]


def _manual_reading_snapshot(row: HomeworkAssignment, payload: dict) -> dict:
    raw_passages = payload.get("passages")
    passages: list[dict] = []
    if isinstance(raw_passages, list) and raw_passages:
        for index, item in enumerate(raw_passages, start=1):
            if not isinstance(item, dict):
                continue
            passage_id = str(item.get("id") or f"p{index}")
            images = [img for img in (item.get("images") or []) if img]
            paragraphs = _paragraphs_from_payload(item) if (item.get("paragraphs") or item.get("passage")) else []
            if not paragraphs and item.get("text"):
                paragraphs = [{"text": str(item.get("text"))}]
            if not paragraphs:
                continue
            passages.append(
                {
                    "id": passage_id,
                    "title": item.get("title") or f"Reading Passage {index}",
                    "paragraphs": paragraphs,
                    "images": images,
                    "diagram_asset": images[0] if images else item.get("diagram_asset"),
                }
            )
    if not passages:
        passage_id = "p1"
        images = [img for img in (payload.get("images") or []) if img]
        passages = [
            {
                "id": passage_id,
                "title": payload.get("passage_title") or "Reading Passage 1",
                "paragraphs": _paragraphs_from_payload(payload),
                "images": images,
                "diagram_asset": images[0] if images else payload.get("diagram_asset"),
            }
        ]

    default_passage_id = passages[0]["id"]
    questions = []
    for i, q in enumerate(payload.get("questions") or [], start=1):
        qtype = _map_manual_reading_qtype(q.get("type") or "")
        options = None
        if q.get("options"):
            options = [{"code": chr(65 + idx), "text": opt} for idx, opt in enumerate(q["options"]) if str(opt).strip()]
        passage_id = str(q.get("passage_id") or default_passage_id)
        if not any(p["id"] == passage_id for p in passages):
            passage_id = default_passage_id
        entry = {
            "id": f"q{i}",
            "number": i,
            "passage_id": passage_id,
            "type": qtype,
            "instruction": q.get("instruction") or None,
            "prompt": q.get("text") or q.get("prompt") or "",
            "options": options,
            "answer": q.get("answer"),
            "word_limit": q.get("word_limit"),
        }
        if q.get("visual_asset") or q.get("image"):
            entry["visual_asset"] = q.get("visual_asset") or q.get("image")
        questions.append(entry)

    test_type = str(payload.get("test_type") or "academic")
    duration = 60 if len(passages) > 1 else 20
    return {
        "id": f"manual-read-{row.id}",
        "title": row.title,
        "test_type": test_type,
        "duration_minutes": duration,
        "passages": passages,
        "questions": questions,
        "question_count": len(questions),
        "instructions": [
            "You should spend about 20 minutes on each passage.",
            "Answers must come from the passage. Spelling counts.",
        ],
    }


def _manual_listening_snapshot(row: HomeworkAssignment, payload: dict) -> dict:
    part_id = "part1"
    questions = []
    for i, q in enumerate(payload.get("questions") or [], start=1):
        qtype = "multiple_choice" if "Multiple" in (q.get("type") or "") else "short_answer"
        options = None
        if q.get("options"):
            options = [{"code": chr(65 + idx), "text": opt} for idx, opt in enumerate(q["options"]) if opt]
        questions.append(
            {
                "id": f"q{i}",
                "number": i,
                "part_id": part_id,
                "part_number": 1,
                "type": qtype,
                "prompt": q.get("text") or "",
                "options": options,
                "answer": q.get("answer"),
            }
        )
    return {
        "id": f"manual-listen-{row.id}",
        "title": row.title,
        "duration_minutes": 10,
        "parts": [
            {
                "id": part_id,
                "part_number": 1,
                "title": "Section 1",
                "audio_file": payload.get("audio_filename"),
            }
        ],
        "questions": questions,
        "question_count": len(questions),
    }
