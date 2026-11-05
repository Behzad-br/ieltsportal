"""
Module 1 foundation probe: config summary + DB connect + read + write + delete.

Usage (from backend/ with venv active):
    set PYTHONPATH=.
    python -m app.cli.check_foundation

Exit codes: 0 = ok, 1 = failure.
Never prints secrets (passwords, JWT, API keys, full DATABASE_URL).
"""
from __future__ import annotations

import sys
import uuid


def _safe_db_label(url: str) -> str:
    from app.db_security import redact_database_url

    return redact_database_url(url)


def main() -> int:
    from app.config import settings
    from app.database import SessionLocal, check_db, init_db
    from app.auth.models import Branch
    from app.db_security import assert_safe_database_target, redact_secrets

    # Refuse pytest/test processes from writing foundation probes to Neon.
    try:
        assert_safe_database_target(
            settings.DATABASE_URL,
            purpose="foundation probe write/delete",
            allow_managed=False,
            environment=settings.environment_name,
        )
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    print("=== IELTS LMS foundation check ===")
    print(f"ENV / NODE_ENV : {(settings.ENV or settings.NODE_ENV or '(empty)')!r}")
    print(f"ENVIRONMENT    : {settings.environment_name}")
    print(f"AUTO_CREATE    : {settings.auto_create_schema}")
    print(f"is_production  : {settings.is_production}")
    print(f"PORT           : {settings.PORT}")
    print(f"FRONTEND_URL   : {settings.FRONTEND_URL}")
    print(f"CORS           : {', '.join(settings.cors_origin_list())}")
    print(f"DATABASE       : {_safe_db_label(settings.DATABASE_URL)}")
    print(f"DATA_DIR       : {settings.DATA_DIR or '(project data/)'}")
    print("")

    if not check_db():
        print("FAIL: cannot connect to database (SELECT 1).")
        print("Fix DATABASE_URL or DB_* in repo-root .env / backend/.env")
        return 1
    print("OK: database connect (SELECT 1)")

    try:
        init_db()
        if settings.auto_create_schema:
            print("OK: schema init (create_all + additive migrations)")
        else:
            print("OK: schema verified (Alembic-managed; no create_all)")
    except Exception as exc:
        print(f"FAIL: init_db — {redact_secrets(str(exc))}")
        return 1

    probe_id = f"_foundation_probe_{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        # READ: count existing branches
        before = db.query(Branch).count()
        print(f"OK: database read (auth_branches count={before})")

        # WRITE
        row = Branch(id=probe_id, name="Foundation Probe Campus", city="Local", image_url="")
        db.add(row)
        db.commit()
        found = db.query(Branch).filter_by(id=probe_id).first()
        if not found:
            print("FAIL: write succeeded in commit but row not readable")
            return 1
        print(f"OK: database write (inserted {probe_id})")

        # DELETE probe (cleanup)
        db.delete(found)
        db.commit()
        gone = db.query(Branch).filter_by(id=probe_id).first()
        if gone:
            print("FAIL: could not delete probe row")
            return 1
        print("OK: database delete (probe cleaned up)")
    except Exception as exc:
        db.rollback()
        print(f"FAIL: read/write probe — {redact_secrets(str(exc))}")
        return 1
    finally:
        db.close()

    print("")
    print("Foundation check PASSED.")
    print("Next: start backend on :5000 and frontend on :5173")
    return 0


if __name__ == "__main__":
    sys.exit(main())
