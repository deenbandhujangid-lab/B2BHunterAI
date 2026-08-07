"""Job run tokens + pause flags — prevents race when restarting a running job."""

from __future__ import annotations

from app.services.run_session import RunSession

_paused_jobs: set[int] = set()
# After Stop: harvest must wait for explicit Start (Load More must not auto-scrape)
_harvest_hold: set[int] = set()
_run_tokens: dict[int, int] = {}
_list_tokens: dict[int, int] = {}
_run_sessions: dict[int, RunSession] = {}
_harvest_running: set[int] = set()
_list_running: set[int] = set()


def begin_run(job_id: int) -> int:
    """Start a new harvest run generation. Clears session — orchestrator reloads from DB."""
    _paused_jobs.discard(job_id)
    _harvest_hold.discard(job_id)
    token = _run_tokens.get(job_id, 0) + 1
    _run_tokens[job_id] = token
    _run_sessions[job_id] = RunSession()
    return token


def begin_list(job_id: int) -> int:
    """Start company-load run. Does not release harvest hold (Start is required for scrape)."""
    _paused_jobs.discard(job_id)
    token = _list_tokens.get(job_id, 0) + 1
    _list_tokens[job_id] = token
    _list_running.add(job_id)
    return token


def clear_list(job_id: int) -> None:
    _list_running.discard(job_id)


def cancel_list(job_id: int) -> None:
    """Invalidate listing run so Load More worker stops (even before process kill)."""
    _list_tokens[job_id] = _list_tokens.get(job_id, 0) + 1
    _list_running.discard(job_id)


def is_list_running(job_id: int) -> bool:
    return job_id in _list_running


def should_stop_list(job_id: int, list_token: int) -> bool:
    if job_id in _paused_jobs:
        return True
    return _list_tokens.get(job_id, 0) != list_token


def hold_harvest(job_id: int) -> None:
    """User Stop — scrape stays off until Start."""
    _harvest_hold.add(job_id)


def release_harvest_hold(job_id: int) -> None:
    _harvest_hold.discard(job_id)


def is_harvest_held(job_id: int) -> bool:
    return job_id in _harvest_hold


def mark_harvest_running(job_id: int, running: bool) -> None:
    if running:
        _harvest_running.add(job_id)
    else:
        _harvest_running.discard(job_id)


def is_harvest_running(job_id: int) -> bool:
    return job_id in _harvest_running


def set_run_session(job_id: int, session: RunSession) -> None:
    _run_sessions[job_id] = session


def get_run_session(job_id: int) -> RunSession:
    return _run_sessions.get(job_id, RunSession())


def clear_run_session(job_id: int) -> None:
    _run_sessions.pop(job_id, None)


def get_run_token(job_id: int) -> int:
    return _run_tokens.get(job_id, 0)


def request_pause(job_id: int) -> None:
    _paused_jobs.add(job_id)


def clear_pause(job_id: int) -> None:
    _paused_jobs.discard(job_id)


def is_pause_requested(job_id: int) -> bool:
    return job_id in _paused_jobs


def should_stop(job_id: int, run_token: int) -> bool:
    """True if user paused OR a newer Start/Restart superseded this harvest run."""
    if job_id in _paused_jobs:
        return True
    return _run_tokens.get(job_id, 0) != run_token
