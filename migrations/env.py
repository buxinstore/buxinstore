import logging
from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
import os  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

# Get database URL from environment variable directly (for migrations)
# This must be set BEFORE importing the app, as the app initialization
# requires SQLALCHEMY_DATABASE_URI to be set
database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Please set it in your .env file or environment before running migrations."
    )

# Set the database URL in the environment so the app can use it
os.environ["DATABASE_URL"] = database_url

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception as exc:  # pragma: no cover - defensive logging only
        logging.getLogger(__name__).debug(
            "Alembic logging configuration skipped: %s", exc
        )

config.set_main_option("sqlalchemy.url", database_url)

# Now import app and db - the database URL is already set in the environment
from app import app  # noqa: E402
from app.extensions import db  # noqa: E402

target_metadata = db.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


# Run migrations only if context is properly initialized
# This check prevents errors when the module is imported outside of Alembic commands
if context is not None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
