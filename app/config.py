"""Application configuration.

All secrets come from environment variables (loaded from `.env` in development).
Nothing secret is stored in the database or sent to the browser.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"

# Load .env before the Config classes below read os.environ. (The `flask` CLI loads
# it on its own, but `python run.py` and scripts don't.) Real env vars take priority.
load_dotenv(BASE_DIR / ".env")


def _database_url():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{(INSTANCE_DIR / 'schoolsync.db').as_posix()}"
    # Supabase / Heroku style URLs use the old "postgres://" scheme.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sessions & cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # CSRF (Flask-WTF). Token is sent by JS in the X-CSRFToken header.
    WTF_CSRF_TIME_LIMIT = None

    # Rate limiting (Flask-Limiter)
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True

    # Uploads (knowledge documents, avatars)
    UPLOAD_FOLDER = INSTANCE_DIR / "uploads"
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB

    # Legacy text classifier trained by train_model.py
    CLASSIFIER_PATH = BASE_DIR / "models" / "school_assistant_model.joblib"

    # Account lockout
    MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
    LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
