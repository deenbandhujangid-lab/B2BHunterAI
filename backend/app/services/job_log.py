"""Append-only step logs per job (file + activity helper)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


def _log_dir() -> Path:
    d = settings.BASE_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_log(job_id: int, event: str, detail: str = "") -> None:
    """Write one step line to logs/job_{id}.log and standard logger."""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [{event}] {detail}".rstrip()
    path = _log_dir() / f"job_{job_id}.log"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        logger.debug("job_log write failed", exc_info=True)
    logger.info("job=%s %s %s", job_id, event, detail)
