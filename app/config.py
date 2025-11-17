import os
from datetime import timedelta


class Config:
    """Base Flask configuration shared across environments."""

    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-key-change-this-in-production"

    # Use DATABASE_URL directly without rewriting, fallbacks, or driver switching.
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Connection pool settings to handle SSL connection errors
    # pool_pre_ping: Test connections before using them (handles stale connections)
    # pool_recycle: Recycle connections after 1 hour (prevents SSL timeout issues)
    # pool_size: Number of connections to maintain in the pool
    # max_overflow: Maximum number of connections beyond pool_size
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,  # Test connections before using them
        "pool_recycle": 3600,   # Recycle connections after 1 hour
        "pool_size": 10,        # Number of connections to maintain
        "max_overflow": 20,     # Maximum overflow connections
        "connect_args": {
            "connect_timeout": 10,  # Connection timeout in seconds
            "sslmode": "require",   # Require SSL for PostgreSQL
            "keepalives": 1,        # Enable TCP keepalives
            "keepalives_idle": 30,  # Start keepalives after 30 seconds of idle
            "keepalives_interval": 10,  # Send keepalives every 10 seconds
            "keepalives_count": 5,  # Close connection after 5 failed keepalives
        }
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
    SQLALCHEMY_DATABASE_URI = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    WTF_CSRF_ENABLED = False
