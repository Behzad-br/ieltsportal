"""Apply database schema changes.

Preferred (staging/production):
    cd backend
    alembic upgrade head

Legacy bootstrap (development / empty local DB only):
    python -m app.cli.migrate
    python -m app.cli.migrate --force-create

Usage (from backend/):
    python -m app.cli.migrate
    python -m app.cli.migrate --alembic
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _safe_url(url: str) -> str:
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest and ":" in rest.split("@", 1)[0]:
        userinfo, hostpart = rest.split("@", 1)
        user = userinfo.split(":", 1)[0]
        return f"{scheme}://{user}:***@{hostpart}"
    return url


def _run_alembic_upgrade() -> int:
    backend = Path(__file__).resolve().parent.parent.parent
    cmd = [sys.executable, "-m", "alembic", "upgrade", "head"]
    print(f"Running: {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(backend))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Database schema migrate helper")
    parser.add_argument(
        "--alembic",
        action="store_true",
        help="Run `alembic upgrade head` (recommended for staging/production).",
    )
    parser.add_argument(
        "--force-create",
        action="store_true",
        help="Force create_all + additive ALTER (dev/empty DB only; not for Neon staging).",
    )
    args = parser.parse_args(argv)

    from app.config import settings
    from app.database import check_db, init_db

    url = settings.DATABASE_URL
    print(f"Migrating database: {_safe_url(url)}")
    print(f"ENVIRONMENT={settings.environment_name} AUTO_CREATE_SCHEMA={settings.auto_create_schema}")

    if not check_db():
        print("ERROR: cannot connect to database. Check DB_* / DATABASE_URL in .env", file=sys.stderr)
        return 1

    if args.alembic or settings.is_managed_schema_env:
        return _run_alembic_upgrade()

    init_db(force_create=args.force_create)
    if args.force_create or settings.auto_create_schema:
        print("OK: schema created / updated (additive only — no destructive migrations).")
    else:
        print("OK: schema verified (no create_all). Prefer: alembic upgrade head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
