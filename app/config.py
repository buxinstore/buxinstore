import logging
import os
from datetime import timedelta

from dotenv import find_dotenv, load_dotenv

logger = logging.getLogger(__name__)

_DOTENV_PATH = find_dotenv(usecwd=True)
if _DOTENV_PATH:
    load_dotenv(_DOTENV_PATH)
else:
    logger.warning(
        "No .env file detected. The application will rely on system environment variables."
    )

def _normalize_postgres_url(raw_url: str) -> str:
    """Ensure the SQLAlchemy URL uses psycopg2 driver and Neon defaults."""
    if not raw_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Provide a valid Neon PostgreSQL connection string."
        )

    if raw_url.startswith(("postgres://", "postgresql://")) and "+psycopg2" not in raw_url:
        raw_url = raw_url.replace("postgres://", "postgresql+psycopg2://", 1)
        raw_url = raw_url.replace("postgresql://", "postgresql+psycopg2://", 1)

    if raw_url.startswith("sqlite"):
        raise RuntimeError("SQLite URLs are no longer supported. Set DATABASE_URL for Neon.")

    return raw_url


def _resolve_database_uri() -> str:
    """Resolve the SQLAlchemy database URI from DATABASE_URL."""
    database_url = os.environ.get("DATABASE_URL")
    normalized = _normalize_postgres_url(database_url)
    os.environ["DATABASE_URL"] = normalized
    return normalized


class Config:
    """Base Flask configuration shared across environments."""

    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-key-change-this-in-production"

    DATABASE_URL = _resolve_database_uri()
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = (
        os.environ.get("SQLALCHEMY_TRACK_MODIFICATIONS", "False").lower() == "true"
    )

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    IS_RENDER = bool(os.environ.get("RENDER"))

    UPLOAD_FOLDER = "static/uploads"
    ALLOWED_EXTENSIONS = {
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "mp4",
        "mov",
        "avi",
        "pdf",
        "docx",
    }
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size

    CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")
    CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL")

    PERMANENT_SESSION_LIFETIME = timedelta(days=7)


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_ECHO = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = _normalize_postgres_url(
        os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    )
    WTF_CSRF_ENABLED = False
