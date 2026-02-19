"""FastAPI application entry point."""

import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import agents, health, results

# ── Logging configuration ────────────────────────────────────────────

def _configure_logging() -> None:
    """Set up root logger with console + file handlers."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # Ensure log directory exists
    log_path = Path(settings.LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(fmt)

    # File handler (append mode so logs persist across restarts)
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # always capture DEBUG to file
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Avoid duplicate handlers on reload
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    # Quiet noisy third-party loggers
    for noisy in ("httpcore", "httpx", "urllib3", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()
_logger = logging.getLogger(__name__)

# ── FastAPI application ──────────────────────────────────────────────

app = FastAPI(
    title="Automated Self-Healing System",
    description="Autonomous DevOps agent for repo analysis, testing, bug classification, fix generation, and CI monitoring.",
    version="0.1.0",
    debug=settings.APP_DEBUG,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ───────────────────────────────────────────────────────────
app.include_router(health.router, tags=["health"])

# Core agent endpoints:  POST /run-agent, GET /status/{run_id}, GET /runs
app.include_router(agents.router, tags=["agents"])

# Legacy results.json reader
app.include_router(results.router, prefix="/api/results", tags=["results"])


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    _logger.info(
        "Starting Self-Healing System | env=%s | log_level=%s | log_file=%s",
        settings.APP_ENV, settings.LOG_LEVEL, settings.LOG_FILE,
    )
