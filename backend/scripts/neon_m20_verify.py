"""Controlled Neon security verification for Module 20 — no credential printing."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth.models import Batch, Branch
from app.config import settings
from app.database import SessionLocal, check_db
from app.db_integrity import MIGRATION_HEAD
from app.db_security import (
    looks_like_managed_cloud_db,
    postgres_sslmode,
    redact_database_url,
    redact_secrets,
)


def main() -> int:
    url = settings.DATABASE_URL or ""
    if not looks_like_managed_cloud_db(url) and "postgresql" not in url.lower():
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print(f"  FAIL: {msg}")
        else:
            print(f"  OK: {msg}")

    print("=== M20 Neon security verification ===")
    print(f"target={redact_database_url(url)}")
    check(check_db(), "database connect (SELECT 1)")
    mode = postgres_sslmode(url)
    check(mode in {"require", "verify-ca", "verify-full"}, f"sslmode={mode!r} is require/verify-*")

    db = SessionLocal()
    tag = uuid.uuid4().hex[:8]
    branch_id = f"m20-sec-{tag}"
    try:
        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        check(head == MIGRATION_HEAD, f"alembic head is {MIGRATION_HEAD} (got {head})")

        who = db.execute(text("SELECT current_user")).scalar()
        dbname = db.execute(text("SELECT current_database()")).scalar()
        # Identity only — never password.
        check(bool(who and dbname), f"session identity user={who} db={dbname}")

        # Transaction rollback: insert then raise path
        db.add(Branch(id=branch_id, name=f"M20 Sec {tag}", city="M20", is_active=True))
        db.flush()
        db.rollback()
        gone = db.query(Branch).filter_by(id=branch_id).first()
        check(gone is None, "explicit rollback discards uncommitted branch")

        # Uniqueness conflict isolation + cleanup
        db.add(Branch(id=branch_id, name=f"M20 Sec {tag}", city="M20", is_active=True))
        db.add(Batch(id=f"bat-{tag}", branch_id=branch_id, name="Morning"))
        db.commit()
        try:
            db.add(Batch(id=f"bat-dup-{tag}", branch_id=branch_id, name="Morning"))
            db.commit()
            check(False, "duplicate batch rejected with IntegrityError")
        except IntegrityError:
            db.rollback()
            check(True, "duplicate batch rejected with IntegrityError")

        # Redaction helper smoke against the live URL (never print it).
        from urllib.parse import urlparse

        sample = f"OperationalError: could not connect to {url}"
        red = redact_secrets(sample)
        pw = urlparse(url).password
        check("***" in red, "redact_secrets masks credential material")
        if pw:
            check(pw not in red, "raw password absent from redacted error text")
            check(pw not in redact_database_url(url), "redact_database_url hides password")


        # Cleanup fixtures only
        db.query(Batch).filter_by(id=f"bat-{tag}").delete()
        db.query(Branch).filter_by(id=branch_id).delete()
        db.commit()
        check(
            db.query(Branch).filter_by(id=branch_id).first() is None,
            "temp fixtures cleaned",
        )

        if failures:
            print(f"=== M20 Neon FAILED ({len(failures)}) ===")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("M20_NEON_SECURITY_PASS")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"FAIL: {redact_secrets(str(exc))}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
