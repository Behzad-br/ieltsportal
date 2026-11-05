"""Phase 3 — Alembic baseline + expand-only migration checks (isolated SQLite)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND / "alembic.ini"
BASELINE = "20261012_0001"
HEAD = "20261012_0007"


def _alembic(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    # backend/alembic/ (migration scripts) shadows the installed alembic package when
    # cwd/PYTHONPATH is backend/. Launch via alembic.config with that path stripped.
    backend_posix = BACKEND.as_posix()
    launcher = (
        "import sys\n"
        f"_skip = {{'', '.', r'{BACKEND}', r'{backend_posix}'}}\n"
        "sys.path = [p for p in sys.path if p not in _skip "
        f"and p.replace('\\\\', '/').rstrip('/') != r'{backend_posix}']\n"
        "from alembic.config import main\n"
        f"sys.argv = ['alembic', '-c', r'{ALEMBIC_INI}'] + {list(args)!r}\n"
        "raise SystemExit(main() or 0)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", launcher],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _child_env(db_url: str, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    # Do not put BACKEND on PYTHONPATH — it shadows the installed `alembic` package
    # with backend/alembic/. alembic/env.py inserts BACKEND for `app.*` imports.
    existing = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and Path(p).resolve() != BACKEND]
    env["PYTHONPATH"] = os.pathsep.join(existing)
    # Keep Alembic child processes from inheriting managed-env startup side effects.
    env.setdefault("ENVIRONMENT", "test")
    env.setdefault("AUTO_CREATE_SCHEMA", "false")
    env.update(extra)
    return env


@pytest.fixture()
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'phase3_mig.db').as_posix()}"


def test_empty_db_can_upgrade_to_head(sqlite_url: str):
    """Empty DB: baseline is empty, then migration_metadata is created."""
    env = _child_env(sqlite_url)
    up = _alembic(env, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)
    assert "migration_metadata" in insp.get_table_names()
    assert "alembic_version" in insp.get_table_names()
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == HEAD
        marker = conn.execute(
            text("SELECT value FROM migration_metadata WHERE key='phase3_alembic'")
        ).scalar()
        assert marker == "established"


def test_baseline_shaped_db_stamp_then_upgrade(sqlite_url: str):
    """Simulate existing schema: create_all, stamp baseline, upgrade expand-only."""
    # Import models into metadata inside this process, then create_all on temp DB.
    from app.database import Base
    from app.speaking import models as _s  # noqa: F401
    from app.writing import models as _w  # noqa: F401
    from app.reading import models as _r  # noqa: F401
    from app.listening import models as _l  # noqa: F401
    from app.homework import models as _h  # noqa: F401
    from app.notes import models as _n  # noqa: F401
    from app.mocks import models as _m  # noqa: F401
    from app.auth import models as _a  # noqa: F401

    engine = create_engine(sqlite_url)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        cols = {c["name"] for c in inspect(engine).get_columns("auth_branches")}
        if "is_active" in cols:
            conn.execute(
                text(
                    "INSERT INTO auth_branches (id, name, city, image_url, is_active) "
                    "VALUES ('b1', 'Probe', 'City', '', 1)"
                )
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO auth_branches (id, name, city, image_url) "
                    "VALUES ('b1', 'Probe', 'City', '')"
                )
            )
        before = conn.execute(text("SELECT COUNT(*) FROM auth_branches")).scalar()

    env = _child_env(sqlite_url)
    stamp = _alembic(env, "stamp", BASELINE)
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr

    up = _alembic(env, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr

    with engine.connect() as conn:
        after = conn.execute(text("SELECT COUNT(*) FROM auth_branches")).scalar()
        assert after == before
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == HEAD
        assert "migration_metadata" in inspect(engine).get_table_names()

    # Idempotent upgrade
    again = _alembic(env, "upgrade", "head")
    assert again.returncode == 0, again.stdout + again.stderr
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == HEAD
        assert conn.execute(text("SELECT COUNT(*) FROM auth_branches")).scalar() == before


def test_downgrade_and_reupgrade_preserves_business_rows(sqlite_url: str):
    from app.database import Base
    from app.speaking import models as _s  # noqa: F401
    from app.writing import models as _w  # noqa: F401
    from app.reading import models as _r  # noqa: F401
    from app.listening import models as _l  # noqa: F401
    from app.homework import models as _h  # noqa: F401
    from app.notes import models as _n  # noqa: F401
    from app.mocks import models as _m  # noqa: F401
    from app.auth import models as _a  # noqa: F401

    engine = create_engine(sqlite_url)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO auth_users "
                "(id, username, email, password_hash, role, full_name, is_active) "
                "VALUES ('u1', 'miguser', 'mig@test', 'x', 'student', 'Mig', 1)"
            )
        )
        users_before = conn.execute(text("SELECT COUNT(*) FROM auth_users")).scalar()

    env = _child_env(sqlite_url)
    assert _alembic(env, "stamp", BASELINE).returncode == 0
    assert _alembic(env, "upgrade", "head").returncode == 0
    assert _alembic(env, "downgrade", BASELINE).returncode == 0

    insp = inspect(engine)
    assert "migration_metadata" not in insp.get_table_names()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM auth_users")).scalar() == users_before
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == BASELINE

    assert _alembic(env, "upgrade", "head").returncode == 0
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM auth_users")).scalar() == users_before
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == HEAD


def test_auto_create_schema_false_skips_create_all(monkeypatch, tmp_path: Path):
    """Staging-like settings: init_db must not call create_all when schema missing check fails clearly."""
    from app import config as config_module
    from app import database as db_module

    monkeypatch.setattr(config_module.settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(config_module.settings, "AUTO_CREATE_SCHEMA", "false")
    monkeypatch.setattr(config_module.settings, "REQUIRE_MIGRATIONS_AT_HEAD", "false")

    # Point engine at empty sqlite file with no tables.
    url = f"sqlite:///{(tmp_path / 'empty_staging.db').as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "_IS_SQLITE", True)

    assert config_module.settings.auto_create_schema is False
    with pytest.raises(RuntimeError, match="Required database tables are missing"):
        db_module.init_db()
