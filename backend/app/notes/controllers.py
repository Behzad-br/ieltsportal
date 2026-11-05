"""Teacher notes upload + lock/unlock; students only receive watermarked page images."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.auth.access import find_batch_on_branch, student_batch_from_db, users_in_branch
from app.auth.deps import get_optional_user
from app.auth.models import User
from app.config import settings
from app.database import get_db
from app.homework.controllers import get_student_id, require_teacher
from app.notes.documents import (
    ALLOWED_LABEL,
    extension_of,
    file_kind,
    inspect_document,
    is_allowed_filename,
    media_type_for,
    render_page,
)
from app.notes.models import ClassNote
from app.notes.seed import ensure_sample_note

router = APIRouter()

MAX_NOTE_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
inspect_pdf = inspect_document


def _notes_dir() -> Path:
    dest = Path(settings.NOTES_DIR)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _safe_note_path(note: ClassNote) -> Path:
    """Resolve stored file under NOTES_DIR; reject path traversal."""
    root = _notes_dir().resolve()
    name = Path(note.stored_name or "").name
    if not name or name != (note.stored_name or ""):
        raise HTTPException(400, "Invalid stored filename.")
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(400, "Invalid stored filename.") from exc
    return path


def _public(note: ClassNote, unlocked: Optional[bool] = None, *, for_student: bool = False) -> dict:
    kind = file_kind(note.original_name or note.stored_name)
    data = {
        "id": note.id,
        "title": note.title,
        "description": note.description or "",
        "original_name": note.original_name,
        "kind": kind,
        "page_count": note.page_count,
        "unlocked": unlocked,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }
    if not for_student:
        data["unlocked_batches"] = note.unlocked_batches
        data["unlocked_student_ids"] = note.unlocked_student_ids
        data["created_by"] = note.created_by
    return data


def student_aliases(student_id: str) -> set[str]:
    """Legacy demo id aliases — only when AUTH_LEGACY_HEADERS is enabled."""
    key = (student_id or "").strip().lower()
    ids = {key} if key else set()
    if settings.AUTH_LEGACY_HEADERS and key in {"local", "s1", "ali.ahmad"}:
        ids.update({"local", "s1", "ali.ahmad"})
    return {item for item in ids if item}


def _creator_branch_id(db: Session, note: ClassNote) -> str:
    if not note.created_by:
        return ""
    creator = db.query(User).filter_by(id=note.created_by).first()
    return (creator.branch_id or "").strip() if creator else ""


def student_can_read(db: Session, note: ClassNote, student_id: str, batch: str = "") -> bool:
    """
    Student may open a note when:
    - their id is in unlocked_student_ids, OR
    - their DB batch is unlocked AND they share the creator's campus
      (blocks same-name batch leakage across branches).
    """
    aliases = student_aliases(student_id)
    unlocked_ids = {item.strip().lower() for item in note.unlocked_student_ids if item}
    if aliases & unlocked_ids:
        return True

    batch_key = (batch or "").strip().lower()
    if not batch_key:
        return False
    unlocked_batches = {item.strip().lower() for item in note.unlocked_batches if item}
    if batch_key not in unlocked_batches:
        return False

    student = db.query(User).filter_by(id=student_id, role="student").first()
    student_branch = (student.branch_id or "").strip() if student else ""
    creator_branch = _creator_branch_id(db, note)
    if creator_branch:
        return bool(student_branch) and student_branch == creator_branch
    # Legacy token/system rows without a real staff creator user.
    if settings.AUTH_LEGACY_HEADERS:
        return True
    if (note.created_by or "") in {"system", "teacher", ""}:
        # System sample / legacy: deny cross-campus by requiring a campus student row.
        return bool(student_branch)
    return False


def _staff_user(
    user: User | None = Depends(get_optional_user),
    _: str = Depends(require_teacher),
) -> User | None:
    if user and user.role in {"teacher", "branch_admin", "super_admin"}:
        return user
    return None


def _note_touches_branch(db: Session, note: ClassNote, branch_id: str) -> bool:
    if not branch_id:
        return False
    creator_branch = _creator_branch_id(db, note)
    if creator_branch and creator_branch == branch_id:
        return True
    unlocked = {s.strip() for s in (note.unlocked_student_ids or []) if str(s).strip()}
    allowed = set(users_in_branch(db, branch_id, role="student"))
    if unlocked & allowed:
        return True
    batches = [b.strip() for b in (note.unlocked_batches or []) if str(b).strip()]
    if batches:
        return (
            db.query(User)
            .filter(
                User.role == "student",
                User.branch_id == branch_id,
                User.is_active == True,  # noqa: E712
                User.batch.in_(batches),
            )
            .first()
            is not None
        )
    return False


def _note_visible_to_staff(db: Session, note: ClassNote, actor: User | None) -> bool:
    """Teacher: own rows (+ legacy/system that touch own branch). BA: branch. SA/token: all."""
    if actor is None or actor.role == "super_admin":
        return True
    if actor.role == "teacher":
        if (note.created_by or "") == actor.id:
            return True
        if (note.created_by or "") in {"teacher", "", "system"}:
            return _note_touches_branch(db, note, actor.branch_id or "")
        return False
    if actor.role == "branch_admin":
        if (note.created_by or "") == actor.id:
            return True
        return _note_touches_branch(db, note, actor.branch_id or "")
    return False


def _require_note_access(db: Session, note: ClassNote, actor: User | None) -> None:
    if not _note_visible_to_staff(db, note, actor):
        raise HTTPException(403, "Not allowed for these notes.")


def _validate_unlock_targets(
    db: Session,
    actor: User | None,
    batches: list,
    unlocked_students: list[str],
) -> tuple[list[str], list[str]]:
    cleaned_batches = [str(item).strip() for item in batches if str(item).strip()]
    if actor and actor.role in {"teacher", "branch_admin"} and unlocked_students:
        allowed = set(users_in_branch(db, actor.branch_id or "", role="student"))
        foreign = [sid for sid in unlocked_students if sid not in allowed]
        if foreign:
            raise HTTPException(403, "Cannot unlock students outside your branch.")
    if actor and actor.role in {"teacher", "branch_admin"} and cleaned_batches:
        bid = actor.branch_id or ""
        for label in cleaned_batches:
            on_campus = find_batch_on_branch(db, bid, label) is not None
            has_students = (
                db.query(User)
                .filter_by(role="student", branch_id=bid, batch=label, is_active=True)
                .first()
                is not None
            )
            if not on_campus and not has_students:
                raise HTTPException(400, f"Batch '{label}' is not on your campus.")
    return cleaned_batches, unlocked_students


async def _save_file(file: UploadFile) -> tuple[str, str, int]:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(raw) > MAX_NOTE_BYTES:
        raise HTTPException(400, f"File must be under {settings.MAX_UPLOAD_SIZE_MB} MB.")
    name = file.filename or "notes.txt"
    # Reject path components in original filename.
    safe_name = Path(name).name
    if not safe_name or safe_name != name.replace("\\", "/").split("/")[-1]:
        raise HTTPException(400, "Invalid filename.")
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(400, "Invalid filename.")
    if not is_allowed_filename(safe_name):
        raise HTTPException(400, f"Upload a document ({ALLOWED_LABEL}). Executables are not allowed.")
    ext = extension_of(safe_name)
    stored = f"{uuid.uuid4().hex}{ext}"
    path = _notes_dir() / stored
    path.write_bytes(raw)
    try:
        pages = inspect_document(path)
    except Exception:
        pages = 1
    return safe_name, stored, max(1, pages)


@router.post("/notes/teacher")
async def create_note(
    title: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    teacher_key: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    title = title.strip()
    if len(title) < 2:
        raise HTTPException(400, "Enter a notes title.")
    original, stored, pages = await _save_file(file)
    note = ClassNote(
        title=title,
        description=description.strip(),
        original_name=original,
        stored_name=stored,
        page_count=pages,
        created_by=(actor.id if actor else teacher_key),
    )
    note.unlocked_batches = []
    note.unlocked_student_ids = []
    db.add(note)
    try:
        db.commit()
    except Exception:
        # Compensating cleanup: file was written before DB row existed.
        orphan = _notes_dir() / stored
        if orphan.exists():
            try:
                orphan.unlink()
            except OSError:
                pass
        raise
    db.refresh(note)
    return _public(note, unlocked=False)


@router.get("/notes/teacher")
async def list_teacher_notes(
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    ensure_sample_note(db)
    rows = db.query(ClassNote).order_by(ClassNote.updated_at.desc()).all()
    return {"notes": [_public(row) for row in rows if _note_visible_to_staff(db, row, actor)]}


@router.put("/notes/teacher/{note_id}")
async def update_note(
    note_id: str,
    title: str = Form(...),
    description: str = Form(""),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    note = db.query(ClassNote).filter(ClassNote.id == note_id).first()
    if not note:
        raise HTTPException(404, "Notes not found.")
    _require_note_access(db, note, actor)
    title = title.strip()
    if len(title) < 2:
        raise HTTPException(400, "Enter a notes title.")
    note.title = title
    note.description = description.strip()
    note.updated_at = datetime.utcnow()
    old_path = None
    if file and file.filename:
        original, stored, pages = await _save_file(file)
        old_path = _safe_note_path(note)
        note.original_name = original
        note.stored_name = stored
        note.page_count = pages
    try:
        db.commit()
    except Exception:
        if file and file.filename:
            orphan = _notes_dir() / note.stored_name
            if orphan.exists():
                try:
                    orphan.unlink()
                except OSError:
                    pass
        raise
    # Remove previous file only after DB points at the new one.
    if old_path is not None and old_path.exists() and old_path.name != note.stored_name:
        try:
            old_path.unlink()
        except OSError:
            pass
    db.refresh(note)
    return _public(note)


@router.put("/notes/teacher/{note_id}/access")
async def set_note_access(
    note_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    note = db.query(ClassNote).filter(ClassNote.id == note_id).first()
    if not note:
        raise HTTPException(404, "Notes not found.")
    _require_note_access(db, note, actor)
    batches = body.get("unlocked_batches") or []
    students = body.get("unlocked_student_ids") or []
    if not isinstance(batches, list) or not isinstance(students, list):
        raise HTTPException(400, "Access lists must be arrays.")
    unlocked_students = [str(item).strip() for item in students if str(item).strip()]
    cleaned_batches, unlocked_students = _validate_unlock_targets(
        db, actor, batches, unlocked_students
    )
    note.unlocked_batches = cleaned_batches
    note.unlocked_student_ids = unlocked_students
    note.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return _public(note)


@router.delete("/notes/teacher/{note_id}")
async def delete_note(
    note_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    note = db.query(ClassNote).filter(ClassNote.id == note_id).first()
    if not note:
        raise HTTPException(404, "Notes not found.")
    _require_note_access(db, note, actor)
    path = _safe_note_path(note)
    db.delete(note)
    db.commit()
    if path.exists():
        path.unlink()
    return {"ok": True}


@router.get("/notes/teacher/{note_id}/file")
async def teacher_file(
    note_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_teacher),
    actor: User | None = Depends(_staff_user),
):
    note = db.query(ClassNote).filter(ClassNote.id == note_id).first()
    if not note:
        raise HTTPException(404, "Notes not found.")
    _require_note_access(db, note, actor)
    path = _safe_note_path(note)
    if not path.exists():
        raise HTTPException(404, "File is missing.")
    return FileResponse(
        path,
        media_type=media_type_for(note.original_name or note.stored_name),
        filename=note.original_name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/notes/student")
async def list_student_notes(
    db: Session = Depends(get_db),
    student_id: str = Depends(get_student_id),
):
    ensure_sample_note(db)
    batch = student_batch_from_db(db, student_id)
    rows = db.query(ClassNote).order_by(ClassNote.updated_at.desc()).all()
    # Do not enumerate locked/foreign-campus notes (metadata IDOR).
    visible = [row for row in rows if student_can_read(db, row, student_id, batch)]
    return {
        "notes": [
            {
                "id": row.id,
                "title": row.title,
                "description": row.description or "",
                "page_count": row.page_count,
                "kind": file_kind(row.original_name or row.stored_name),
                "unlocked": True,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in visible
        ]
    }


@router.get("/notes/student/{note_id}")
async def student_note_meta(
    note_id: str,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_student_id),
    x_student_name: Optional[str] = Header(default="", alias="X-Student-Name"),
):
    note = db.query(ClassNote).filter(ClassNote.id == note_id).first()
    if not note:
        raise HTTPException(404, "Notes not found.")
    batch = student_batch_from_db(db, student_id)
    if not student_can_read(db, note, student_id, batch):
        raise HTTPException(403, "These notes are locked. Ask your teacher to unlock them.")
    return {
        **_public(note, unlocked=True, for_student=True),
        "watermark": f"{(x_student_name or student_id).strip() or student_id} · view only",
    }


@router.get("/notes/student/{note_id}/page/{page_number}")
async def student_note_page(
    note_id: str,
    page_number: int,
    db: Session = Depends(get_db),
    student_id: str = Depends(get_student_id),
    x_student_name: Optional[str] = Header(default="", alias="X-Student-Name"),
):
    note = db.query(ClassNote).filter(ClassNote.id == note_id).first()
    if not note:
        raise HTTPException(404, "Notes not found.")
    batch = student_batch_from_db(db, student_id)
    if not student_can_read(db, note, student_id, batch):
        raise HTTPException(403, "These notes are locked. Ask your teacher to unlock them.")
    path = _safe_note_path(note)
    if not path.exists():
        raise HTTPException(404, "File is missing.")
    mark = f"{(x_student_name or student_id).strip() or student_id}  ·  {student_id}  ·  VIEW ONLY"
    try:
        png = render_page(path, page_number, mark)
    except IndexError:
        raise HTTPException(404, "Page not found.")
    except Exception as exc:
        raise HTTPException(500, f"Could not open this page: {exc}") from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline; filename=notes-page.png",
            "X-Frame-Options": "DENY",
        },
    )
