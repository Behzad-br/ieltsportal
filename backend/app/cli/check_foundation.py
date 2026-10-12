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
from urllib.parse import urlparse


def _safe_db_label(url: str) -> str:
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme or "unknown"
        host = parsed.hostname or ("file" if scheme.startswith("sqlite") else "unknown")
        db = (parsed.path or "").lstrip("/") or "(default)"
        return f"{scheme}://{host}/{db}"
    except Exception:
        return "(unparseable)"


def main() -> int:
    from app.config import settings
    from app.database import SessionLocal, check_db, init_db
    from app.auth.models import Branch

    print("=== IELTS LMS foundation check ===")
    print(f"ENV / NODE_ENV : {(settings.ENV or settings.NODE_ENV or '(empty)')!r}")
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
        print("OK: schema init (create_all + additive migrations)")
    except Exception as exc:
        print(f"FAIL: init_db — {exc}")
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
        print(f"FAIL: read/write probe — {exc}")
        return 1
    finally:
        db.close()

    print("")
    print("Foundation check PASSED.")
    print("Next: start backend on :5000 and frontend on :5173")
    return 0


if __name__ == "__main__":
    sys.exit(main())
