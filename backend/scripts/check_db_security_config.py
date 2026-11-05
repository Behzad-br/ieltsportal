"""Inspect database security configuration WITHOUT printing secrets.

Usage (from backend/):
  set PYTHONPATH=.
  python scripts/check_db_security_config.py

Exit 0 = checks pass for current ENVIRONMENT; 1 = failures.
"""
from __future__ import annotations

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

    def check(cond: bool, ok: str, bad: str) -> None:
        if cond:
            print(f"  OK: {ok}")
        else:
            failures.append(bad)
            print(f"  FAIL: {bad}")

    print("=== DB security config (secrets redacted) ===")
    print(f"ENVIRONMENT     : {settings.environment_name}")
    print(f"is_production   : {settings.is_production}")
    print(f"is_staging      : {settings.is_staging}")
    print(f"DATABASE        : {redact_database_url(settings.DATABASE_URL)}")
    print(f"sslmode         : {postgres_sslmode(settings.DATABASE_URL)}")
    print(f"AUTO_CREATE     : {settings.auto_create_schema}")
    print(f"LEGACY_HEADERS  : {settings.AUTH_LEGACY_HEADERS}")
    print(f"JWT default?    : {(settings.JWT_SECRET or '').startswith('change-me')}")
    print(f"managed cloud?  : {looks_like_managed_cloud_db(settings.DATABASE_URL)}")
    print("")

    check(bool(settings.DATABASE_URL), "DATABASE_URL present", "DATABASE_URL missing")
    check(
        not (settings.is_production and (settings.JWT_SECRET or "").startswith("change-me")),
        "JWT_SECRET not placeholder (or non-prod)",
        "Production JWT_SECRET is still the default placeholder",
    )
    check(
        not (settings.is_production and settings.AUTH_LEGACY_HEADERS),
        "AUTH_LEGACY_HEADERS false in production (or non-prod)",
        "AUTH_LEGACY_HEADERS must be false in production",
    )

    try:
        assert_no_auto_create_in_managed(settings.environment_name, settings.auto_create_schema)
        check(True, "AUTO_CREATE_SCHEMA policy OK for environment", "")
    except RuntimeError as exc:
        check(False, "", str(exc))

    try:
        assert_postgres_tls_policy(settings.DATABASE_URL, environment=settings.environment_name)
        if is_postgres_url(settings.DATABASE_URL) and settings.is_managed_schema_env:
            check(True, "Postgres sslmode require/verify-* for managed env", "")
        else:
            print("  OK: TLS policy N/A (non-Postgres or non-managed env)")
    except RuntimeError as exc:
        check(False, "", str(exc))

    # echo=False is hard-coded in database.py — state it explicitly.
    print("  OK: SQLAlchemy echo=False (no SQL value dumping)")

    if failures:
        print(f"=== FAILED ({len(failures)}) ===")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("=== DB security config PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
