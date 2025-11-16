import os
from datetime import timedelta


class Config:
    """Base Flask configuration shared across environments."""

    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-key-change-this-in-production"

    # Use DATABASE_URL directly without rewriting, fallbacks, or driver switching.
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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
