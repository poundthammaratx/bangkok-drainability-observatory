"""Alembic environment.

The database URL is never read from alembic.ini (no credentials are committed to the repo — see
docs/DATABASE_DEPLOYMENT.md). It comes from the same ``bdo.config.load_settings()`` the
application itself uses, so ``BDO_DATABASE_URL`` / ``.env`` / ``config/settings.yaml`` are the
single source of truth for both the app and its migrations.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bdo.config import load_settings  # noqa: E402
from bdo.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    # -x db_url=... on the CLI overrides the configured settings (useful for one-off tooling);
    # the normal path is always bdo.config.load_settings().
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("db_url") or load_settings().database_url


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg_section = config.get_section(config.config_ini_section, {})
    cfg_section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(cfg_section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
