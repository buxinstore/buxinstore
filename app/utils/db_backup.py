import os
import subprocess
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Tuple

from flask import current_app
from sqlalchemy.engine import make_url


class DatabaseBackupError(RuntimeError):
    """Raised when a PostgreSQL backup action fails."""


def _make_pg_dsn() -> Tuple[str, dict]:
    """Convert the SQLAlchemy URI into a pg_dump friendly DSN."""
    sqlalchemy_url = make_url(current_app.config["SQLALCHEMY_DATABASE_URI"])
    pg_url = sqlalchemy_url.set(drivername="postgresql")
    dsn = pg_url.render_as_string(hide_password=False)
    env = os.environ.copy()
    if pg_url.password:
        env["PGPASSWORD"] = pg_url.password
    return dsn, env


def dump_database_to_memory() -> BytesIO:
    """Return the output of pg_dump as a BytesIO buffer."""
    dsn, env = _make_pg_dsn()
    cmd = [
        os.getenv("PG_DUMP_BIN", "pg_dump"),
        "--dbname",
        dsn,
        "--no-owner",
        "--no-privileges",
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise DatabaseBackupError(
            "pg_dump executable not found. Install PostgreSQL client tools "
            "or set the PG_DUMP_BIN environment variable."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise DatabaseBackupError(exc.stderr.decode("utf-8", errors="ignore")) from exc

    buffer = BytesIO(completed.stdout)
    buffer.seek(0)
    return buffer


def dump_database_to_file(prefix: str = "backup") -> Path:
    """Create a pg_dump file in the configured backup directory."""
    backup_dir = Path(current_app.config.get("BACKUP_DIRECTORY") or current_app.instance_path) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_path = backup_dir / f"{prefix}_{timestamp}.sql"

    dsn, env = _make_pg_dsn()
    cmd = [
        os.getenv("PG_DUMP_BIN", "pg_dump"),
        "--dbname",
        dsn,
        "--no-owner",
        "--no-privileges",
    ]
    try:
        with file_path.open("wb") as fh:
            subprocess.run(cmd, check=True, stdout=fh, stderr=subprocess.PIPE, env=env)
    except FileNotFoundError as exc:
        raise DatabaseBackupError(
            "pg_dump executable not found. Install PostgreSQL client tools "
            "or set the PG_DUMP_BIN environment variable."
        ) from exc
    except subprocess.CalledProcessError as exc:
        error = exc.stderr.decode("utf-8", errors="ignore")
        raise DatabaseBackupError(error or "pg_dump failed") from exc

    return file_path

