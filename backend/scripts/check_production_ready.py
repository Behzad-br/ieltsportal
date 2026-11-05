"""Production / staging readiness validator — never prints secrets.

Usage (from backend/):
  set PYTHONPATH=.
  python scripts/check_production_ready.py

Exit 0 = pass for current ENVIRONMENT; 1 = failures.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db_security import (
    assert_no_auto_create_in_managed,
    assert_postgres_tls_policy,
    is_postgres_url,
    looks_like_managed_cloud_db,
    postgres_sslmode,
    redact_database_url,
)


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    def check(cond: bool, ok: str, bad: str) -> None:
        if cond:
            print(f"  OK: {ok}")
        else:
            failures.append(bad)
            print(f"  FAIL: {bad}")

    def warn(cond: bool, ok: str, note: str) -> None:
        if cond:
            print(f"  OK: {ok}")
        else:
            warnings.append(note)
            print(f"  WARN: {note}")

    print("=== Production readiness (secrets redacted) ===")
    print(f"ENVIRONMENT        : {settings.environment_name}")
    print(f"is_production      : {settings.is_production}")
    print(f"is_staging         : {settings.is_staging}")
    print(f"DATABASE           : {redact_database_url(settings.DATABASE_URL)}")
    print(f"sslmode            : {postgres_sslmode(settings.DATABASE_URL)}")
    print(f"AUTO_CREATE_SCHEMA : {settings.auto_create_schema}")
    print(f"AUTH_LEGACY        : {settings.AUTH_LEGACY_HEADERS}")
    print(f"JWT placeholder?   : {(settings.JWT_SECRET or '').startswith('change-me')}")
    print(f"ALLOW_OPS_TOKEN    : {settings.ALLOW_WRITING_ADMIN_TOKEN}")
    print(f"ops token set?     : {bool((settings.WRITING_ADMIN_TOKEN or '').strip())}")
    print(f"ops token allowed? : {settings.writing_admin_token_allowed}")
    print(f"DATA_DIR           : {settings.DATA_DIR or '(derived)'}")
    print(f"SPEAKING_AUDIO_DIR : {settings.SPEAKING_AUDIO_DIR}")
    print(f"NOTES_DIR          : {settings.NOTES_DIR}")
    print(f"CORS origins count : {len(settings.cors_origin_list())}")
    print(f"LLM configured?    : {bool((settings.LLM_API_KEY or settings.OPENAI_API_KEY or settings.MINIMAX_API_KEY or '').strip())}")
    print(f"LiveKit URL set?   : {bool((settings.LIVEKIT_URL or '').strip())}")
    print("")

    check(bool(settings.DATABASE_URL), "DATABASE_URL present", "DATABASE_URL missing")
    check(
        not (settings.is_production and (settings.JWT_SECRET or "").startswith("change-me")),
        "JWT_SECRET strong (or non-prod)",
        "Production JWT_SECRET is still the default placeholder",
    )
    check(
        not (settings.is_production and settings.AUTH_LEGACY_HEADERS),
        "AUTH_LEGACY_HEADERS false in production (or non-prod)",
        "AUTH_LEGACY_HEADERS must be false in production",
    )
    check(
        not (settings.is_production and settings.writing_admin_token_allowed and not settings.ALLOW_WRITING_ADMIN_TOKEN),
        "Ops token gate consistent",
        "Ops writing admin token unexpectedly allowed",
    )
    if settings.is_production and (settings.WRITING_ADMIN_TOKEN or "").strip() and not settings.ALLOW_WRITING_ADMIN_TOKEN:
        print("  OK: WRITING_ADMIN_TOKEN set but disabled (ALLOW_WRITING_ADMIN_TOKEN=false)")

    try:
        assert_no_auto_create_in_managed(settings.environment_name, settings.auto_create_schema)
        check(True, "AUTO_CREATE_SCHEMA policy OK", "")
    except RuntimeError as exc:
        check(False, "", str(exc))

    try:
        assert_postgres_tls_policy(settings.DATABASE_URL, environment=settings.environment_name)
        if is_postgres_url(settings.DATABASE_URL) and settings.is_managed_schema_env:
            check(True, "Postgres TLS require/verify-*", "")
        else:
            print("  OK: TLS policy N/A (non-Postgres or non-managed)")
    except RuntimeError as exc:
        check(False, "", str(exc))

    origins = settings.cors_origin_list()
    check("*" not in origins, "CORS has no wildcard", "CORS must not use * with credentials")
    if settings.is_production:
        check(bool(origins), "Production CORS origins configured", "Production CORS_ORIGINS empty")

    ffmpeg = shutil.which("ffmpeg")
    warn(bool(ffmpeg), "ffmpeg on PATH", "ffmpeg not found — Speaking transcription will fail until installed")
    try:
        import whisper  # noqa: F401

        warn(True, "openai-whisper importable", "")
    except Exception:
        warn(False, "", "openai-whisper not importable — install requirements.txt for Speaking")

    for label, path in (
        ("SPEAKING_AUDIO_DIR", settings.SPEAKING_AUDIO_DIR),
        ("NOTES_DIR", settings.NOTES_DIR),
        ("HOMEWORK_AUDIO_DIR", settings.HOMEWORK_AUDIO_DIR),
    ):
        p = Path(path)
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".ready_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            check(True, f"{label} writable", "")
        except OSError as exc:
            check(False, "", f"{label} not writable: {exc}")

    if looks_like_managed_cloud_db(settings.DATABASE_URL):
        print("  OK: managed cloud DB detected — confirm Neon PITR / backup owner before prod GO")

    print("  OK: SQLAlchemy echo=False")

    if warnings:
        print(f"=== WARNINGS ({len(warnings)}) ===")
        for w in warnings:
            print(f"  - {w}")

    if failures:
        print(f"=== FAILED ({len(failures)}) ===")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("=== Production readiness PASS (see WARNINGS for ops follow-ups) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
