"""Health controller."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.speaking.schemas import HealthSchema
from app.llm.client import minimax_client

router = APIRouter()


def _ffmpeg_available() -> bool:
    try:
        from app.speaking.services.transcription import _FFMPEG_EXE

        return bool(shutil.which(_FFMPEG_EXE) or Path(_FFMPEG_EXE).is_file())
    except Exception:
        return bool(shutil.which("ffmpeg"))


def _whisper_importable() -> bool:
    try:
        import whisper  # noqa: F401

        return True
    except Exception:
        return False


def _media_dirs_writable() -> bool:
    """Probe configured persistent media roots (create marker then remove)."""
    roots = [
        settings.SPEAKING_AUDIO_DIR,
        settings.NOTES_DIR,
        settings.HOMEWORK_AUDIO_DIR,
        settings.DATA_DIR or "",
    ]
    for raw in roots:
        if not raw:
            continue
        path = Path(raw)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".m24_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError:
            return False
    return True


@router.get("/health", response_model=HealthSchema)
async def health():
    """Process alive + DB connectivity. Returns 503 when the database is unreachable."""
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    ffmpeg_ok = _ffmpeg_available()
    whisper_ok = _whisper_importable()
    media_ok = _media_dirs_writable()
    speaking_ok = ffmpeg_ok and whisper_ok

    body = HealthSchema(
        status="ok" if db_ok else "degraded",
        database=db_ok,
        minimax_configured=minimax_client.is_configured(),
        whisper_model=settings.WHISPER_MODEL,
        app_name=settings.APP_NAME,
        ffmpeg_available=ffmpeg_ok,
        whisper_importable=whisper_ok,
        speaking_deps_ready=speaking_ok,
        media_dirs_writable=media_ok,
    )
    if not db_ok:
        return JSONResponse(status_code=503, content=body.model_dump())
    return body
