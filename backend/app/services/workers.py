"""Spawn list/harvest workers as separate processes (API stays unblocked)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def backend_dir() -> Path:
    # app/services/workers.py → backend/
    return Path(__file__).resolve().parent.parent.parent


def python_exe() -> Path:
    py = backend_dir() / "venv" / "Scripts" / "python.exe"
    return py if py.exists() else Path(sys.executable)


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return int(exit_code.value) == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def kill_process(pid: int | None) -> None:
    if not pid or not process_alive(pid):
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                capture_output=True,
                check=False,
            )
        else:
            os.kill(int(pid), 15)
    except Exception:
        logger.debug("kill_process failed pid=%s", pid, exc_info=True)


def spawn_worker(module: str, job_id: int, log_name: str) -> int:
    """Start `python -m module job_id`. Returns child PID."""
    bdir = backend_dir()
    env = {**os.environ, "PYTHONPATH": str(bdir)}
    creationflags = 0x08000000 if sys.platform.startswith("win") else 0
    log_path = bdir / log_name
    logf = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [str(python_exe()), "-m", module, str(job_id)],
        cwd=str(bdir),
        env=env,
        creationflags=creationflags,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    logger.info("Spawned %s pid=%s job=%s log=%s", module, proc.pid, job_id, log_path)
    return int(proc.pid)


def spawn_harvest(job_id: int) -> int:
    """Spawn at most one harvest worker; kill stale PID first."""
    # Caller should pass current harvest_pid via kill if known; also scan is expensive —
    # just spawn with venv python only.
    return spawn_worker("app.services.harvest_worker", job_id, "harvest_worker.log")


def spawn_load(job_id: int) -> int:
    return spawn_worker("app.services.load_worker", job_id, "load_worker.log")


def spawn_harvest_exclusive(job_id: int, old_pid: int | None = None) -> int:
    kill_process(old_pid)
    return spawn_harvest(job_id)
