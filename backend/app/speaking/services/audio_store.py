"""Durable speaking recording storage — basename-safe, session-scoped."""
from __future__ import annotations

import uuid
from pathlib import Path

from app.config import settings

# Extensions we accept from MediaRecorder / uploads (MIME checked separately).
ALLOWED_EXTENSIONS = {".webm", ".ogg", ".wav", ".mp3", ".m4a", ".mp4", ".mpeg"}


def speaking_audio_root() -> Path:
    root = Path(settings.SPEAKING_AUDIO_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def safe_extension(filename: str | None) -> str:
    ext = Path(str(filename or "audio.webm")).name
    ext = Path(ext).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return ".webm"
    return ext


def mime_allowed(content_type: str | None, filename: str | None) -> bool:
    """Accept known audio MIME types or octet-stream with a known extension."""
    allowed = {str(t).split(";")[0].strip().lower() for t in (settings.ALLOWED_AUDIO_TYPES or [])}
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in allowed:
        return True
    # Browsers sometimes omit MIME — allow known extensions only.
    if not ctype or ctype == "application/octet-stream":
        return Path(str(filename or "")).suffix.lower() in ALLOWED_EXTENSIONS
    return False


def persist_recording(session_id: str, audio_bytes: bytes, filename: str | None, content_type: str | None) -> tuple[str, str]:
    """
    Write bytes under SPEAKING_AUDIO_DIR/{session_id}/{uuid}{ext}.
    Returns (relative_storage_key, mime).
    """
    if not audio_bytes:
        raise ValueError("Empty audio.")
    sid = Path(str(session_id)).name
    if not sid or sid in {".", ".."}:
        raise ValueError("Invalid session id.")
    ext = safe_extension(filename)
    mime = (content_type or "").split(";")[0].strip() or f"audio/{ext.lstrip('.')}"
    folder = speaking_audio_root() / sid
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    dest = folder / name
    dest.write_bytes(audio_bytes)
    # Relative key never includes .. 
    key = f"{sid}/{name}"
    return key, mime


def resolve_recording(storage_key: str | None) -> Path | None:
    """Resolve a stored key to an absolute path under SPEAKING_AUDIO_DIR only."""
    if not storage_key:
        return None
    raw = str(storage_key).replace("\\", "/").lstrip("/")
    if ".." in Path(raw).parts:
        return None
    root = speaking_audio_root()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def delete_recording(storage_key: str | None) -> None:
    path = resolve_recording(storage_key)
    if path and path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
