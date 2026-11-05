"""Alembic env — uses app.config.settings.DATABASE_URL (never hardcode credentials)."""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure `backend/` is on sys.path so `app.*` imports work when running alembic.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

# Import all models so Base.metadata is complete for autogenerate comparisons.
from app.speaking import models as _speaking  # noqa: E402, F401
from app.writing import models as _writing  # noqa: E402, F401
from app.reading import models as _reading  # noqa: E402, F401
from app.listening import models as _listening  # noqa: E402, F401
from app.homework import models as _homework  # noqa: E402, F401
from app.notes import models as _notes  # noqa: E402, F401
from app.mocks import models as _mocks  # noqa: E402, F401
from app.auth import models as _auth  # noqa: E402, F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    url = (settings.DATABASE_URL or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is empty. Set DATABASE_URL (or DB_*) in backend/.env / repo .env."
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL script generation)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
