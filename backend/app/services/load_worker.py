"""Standalone company-load worker — out-of-process so API stays responsive."""

from __future__ import annotations

import asyncio
import logging
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.load_worker <job_id>", file=sys.stderr)
        sys.exit(2)
    job_id = int(sys.argv[1])
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    from app.services.orchestrator import _run_load_companies_blocking

    logging.getLogger(__name__).info("Load-companies worker starting for job %d", job_id)
    _run_load_companies_blocking(job_id)
    logging.getLogger(__name__).info("Load-companies worker finished for job %d", job_id)


if __name__ == "__main__":
    main()
