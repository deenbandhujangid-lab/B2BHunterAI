"""Standalone harvest worker — run out-of-process so API stays responsive on Windows."""

from __future__ import annotations

import asyncio
import logging
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.harvest_worker <job_id>", file=sys.stderr)
        sys.exit(2)
    job_id = int(sys.argv[1])
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    from app.services.orchestrator import _run_harvest_blocking

    logging.getLogger(__name__).info("Harvest worker starting for job %d", job_id)
    _run_harvest_blocking(job_id)
    logging.getLogger(__name__).info("Harvest worker finished for job %d", job_id)


if __name__ == "__main__":
    main()
