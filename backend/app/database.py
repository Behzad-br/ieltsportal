"""
database.py — SQLAlchemy engine + session factory + optional schema bootstrap.

Schema ownership:
- staging/production: Alembic migrations (see backend/alembic/). Startup does NOT
  silently create_all or ALTER unless AUTO_CREATE_SCHEMA=true (emergency only).
- development/test: optional create_all + additive ALTER when AUTO_CREATE_SCHEMA
  is enabled (default outside staging/production).
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import String, create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.schema import Column

from app.config import settings

Base = declarative_base()
logger = logging.getLogger(__name__)


@event.listens_for(Column, "after_parent_attach")
def _mysql_default_string_length(column: Column, _table) -> None:
    """MySQL requires VARCHAR length; keep models portable by defaulting bare String → 255."""
    col_type = column.type
    if isinstance(col_type, String) and col_type.length is None:
        col_type.length = 255


_DATABASE_URL = settings.DATABASE_URL
_IS_SQLITE = _DATABASE_URL.startswith("sqlite:")
_IS_MYSQL = _DATABASE_URL.startswith("mysql:")


def _build_engine():
    if _IS_SQLITE:
        raw = _DATABASE_URL.replace("sqlite:///", "", 1)
        if raw and not raw.startswith(":memory:"):
            Path(raw).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(
            _DATABASE_URL,
            connect_args={"check_same_thread": False},
            echo=False,
        )
    return create_engine(
        _DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=280,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )


engine = _build_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session per request.

    Endpoints own explicit commit(). On unhandled errors, roll back so a
    partial flush cannot leak into a later request on a reused connection.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _import_models() -> None:
    from app.speaking import models as _  # noqa: F401
    from app.writing import models as _writing  # noqa: F401
    from app.reading import models as _reading  # noqa: F401
    from app.listening import models as _listening  # noqa: F401
    from app.homework import models as _homework  # noqa: F401
    from app.notes import models as _notes  # noqa: F401
    from app.mocks import models as _mocks  # noqa: F401
    from app.auth import models as _auth  # noqa: F401


def _apply_additive_alters() -> None:
    """Legacy additive ALTER patches — only when AUTO_CREATE_SCHEMA is enabled."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    def _add_column(table: str, column: str, ddl: str):
        if table not in tables:
            return
        cols = {col["name"] for col in inspector.get_columns(table)}
        if column in cols:
            return
        with engine.begin() as conn:
            conn.execute(text(ddl))

    _add_column("test_sessions", "practice_part", "ALTER TABLE test_sessions ADD COLUMN practice_part INTEGER")
    _add_column("test_sessions", "student_id", "ALTER TABLE test_sessions ADD COLUMN student_id VARCHAR(255)")
    _add_column("reading_attempts", "snapshot_json", "ALTER TABLE reading_attempts ADD COLUMN snapshot_json TEXT")
    _add_column("writing_questions", "book_id", "ALTER TABLE writing_questions ADD COLUMN book_id VARCHAR(255)")
    _add_column("writing_questions", "book_title", "ALTER TABLE writing_questions ADD COLUMN book_title VARCHAR(255)")
    _add_column("writing_questions", "test_number", "ALTER TABLE writing_questions ADD COLUMN test_number INTEGER")
    _add_column("writing_questions", "pack_test_id", "ALTER TABLE writing_questions ADD COLUMN pack_test_id VARCHAR(255)")

    if _IS_SQLITE:
        bool_ddl = "ALTER TABLE mock_attempts ADD COLUMN screen_share_active BOOLEAN DEFAULT 0"
    elif _IS_MYSQL:
        bool_ddl = "ALTER TABLE mock_attempts ADD COLUMN screen_share_active TINYINT(1) DEFAULT 0"
    else:
        bool_ddl = "ALTER TABLE mock_attempts ADD COLUMN screen_share_active BOOLEAN DEFAULT FALSE"
    _add_column("mock_attempts", "screen_share_active", bool_ddl)
    _add_column("mock_attempts", "last_warning", "ALTER TABLE mock_attempts ADD COLUMN last_warning VARCHAR(255)")
    _add_column(
        "mock_attempts",
        "connection_status",
        "ALTER TABLE mock_attempts ADD COLUMN connection_status VARCHAR(255)",
    )
    _add_column(
        "mock_assignments",
        "paper_source",
        "ALTER TABLE mock_assignments ADD COLUMN paper_source VARCHAR(64) DEFAULT 'bank'",
    )
    _add_column(
        "mock_assignments",
        "result_mode",
        "ALTER TABLE mock_assignments ADD COLUMN result_mode VARCHAR(64) DEFAULT 'teacher'",
    )


_REQUIRED_CORE_TABLES = (
    "auth_users",
    "auth_branches",
    "auth_batches",
    "test_sessions",
    "questions",
    "alembic_version",
)


def assert_required_schema_present() -> None:
    """Fail clearly when managed envs are missing required tables (no silent create)."""
    from sqlalchemy import inspect

    _import_models()
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    missing = [t for t in _REQUIRED_CORE_TABLES if t not in present]
    # alembic_version may be absent only before first stamp — still require business tables.
    business_missing = [t for t in missing if t != "alembic_version"]
    if business_missing:
        raise RuntimeError(
            "Required database tables are missing: "
            + ", ".join(business_missing)
            + ". Run `alembic upgrade head` as an explicit deployment step "
            + "(AUTO_CREATE_SCHEMA is disabled in staging/production)."
        )


def get_alembic_config():
    """Build Alembic Config rooted at backend/alembic.ini."""
    from alembic.config import Config

    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    return Config(str(ini))


def get_alembic_revision_state() -> tuple[str | None, str | None]:
    """Return (current, head) revision ids for the configured database."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = get_alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current = context.get_current_revision()
    return current, head


def assert_migrations_at_head() -> None:
    current, head = get_alembic_revision_state()
    if current != head:
        raise RuntimeError(
            f"Database migration revision is not at head "
            f"(current={current!r}, head={head!r}). "
            f"Run `alembic upgrade head` before starting the app."
        )


def init_db(*, force_create: bool = False) -> None:
    """Optionally create tables / apply additive ALTERs, or verify managed schema.

    force_create=True: used by local CLI helpers that explicitly want bootstrap.
    """
    _import_models()

    allow_create = force_create or settings.auto_create_schema
    if allow_create:
        Base.metadata.create_all(bind=engine)
        _apply_additive_alters()
        logger.info(
            "Schema bootstrap applied (create_all + additive ALTER). "
            "env=%s AUTO_CREATE_SCHEMA=%s",
            settings.environment_name,
            settings.AUTO_CREATE_SCHEMA or "(default)",
        )
        return

    logger.info(
        "Skipping create_all/ALTER (Alembic-managed schema). env=%s",
        settings.environment_name,
    )
    assert_required_schema_present()
    if settings.require_migrations_at_head:
        assert_migrations_at_head()


def check_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
