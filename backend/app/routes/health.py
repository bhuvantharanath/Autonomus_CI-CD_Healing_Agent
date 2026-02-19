"""Health check and log viewer endpoints."""

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from app.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "self-healing-system"}


@router.get("/logs", response_class=PlainTextResponse)
async def get_logs(
    tail: int = Query(200, ge=1, le=5000, description="Number of lines from the end"),
):
    """Return the last *tail* lines from the application log file.

    Useful for diagnosing pipeline issues without SSH access.
    """
    log_path = Path(settings.LOG_FILE)
    if not log_path.exists():
        return "No log file found yet. Run a pipeline first."

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[-tail:]
    return "\n".join(selected)


@router.get("/logs/search", response_class=PlainTextResponse)
async def search_logs(
    q: str = Query(..., min_length=1, description="Search term"),
    tail: int = Query(500, ge=1, le=10000),
):
    """Search the log file for lines containing *q*."""
    log_path = Path(settings.LOG_FILE)
    if not log_path.exists():
        return "No log file found yet."

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    q_lower = q.lower()
    matched = [ln for ln in lines[-tail:] if q_lower in ln.lower()]
    if not matched:
        return f"No matches for '{q}' in last {tail} lines."
    return "\n".join(matched)
