"""Application configuration loaded from environment variables."""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_SECRET_KEY: str = "change-me"

    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000

    DATABASE_URL: str = "sqlite:///./shared/data/app.db"

    SANDBOX_IMAGE: str = "python:3.11-slim"
    SANDBOX_TIMEOUT: int = 300
    SANDBOX_MEMORY_LIMIT: str = "512m"
    SANDBOX_CPU_LIMIT: float = 1.0

    GITHUB_TOKEN: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""
    CI_POLL_INTERVAL: int = 60

    GEMINI_API_KEY: str = ""
    GEMINI_API_BASE: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    GEMINI_MODEL: str = "gemini-2.0-flash"

    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "shared/logs/app.log"

    class Config:
        env_file = (".env", "../.env")  # works from both /app (Docker) and backend/ (local)
        env_file_encoding = "utf-8"
        extra = "ignore"  # ignore VITE_* and other frontend-only vars


settings = Settings()

# ── Export key settings to os.environ so agent tools can read them ────
# Tools (error_classifier, patch_applier, fixer) use os.environ.get()
# directly rather than importing settings.
_EXPORT_KEYS = [
    "GEMINI_API_KEY", "GEMINI_API_BASE", "GEMINI_MODEL", "GITHUB_TOKEN",
]
for _key in _EXPORT_KEYS:
    _val = getattr(settings, _key, "")
    if _val and not os.environ.get(_key):
        os.environ[_key] = _val
