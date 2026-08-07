"""Wipe leads + checks; reset company_queue to pending for re-scrape. Keeps jobs/domains/queries."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Allow running as: python scripts/fresh_reset_for_rescrape.py from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select, update

from app.core.database import AsyncSessionLocal, init_db
from app.models import Lead, SearchJob, JobStatus
from app.models.company_check import CompanyCheck
from app.models.company_queue import CompanyQueue, QueueStatus


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        leads_n = (await db.execute(delete(Lead))).rowcount or 0
        checks_n = (await db.execute(delete(CompanyCheck))).rowcount or 0
        queue_n = (
            await db.execute(
                update(CompanyQueue).values(
                    status=QueueStatus.PENDING,
                    last_error=None,
                    checked_at=None,
                )
            )
        ).rowcount or 0
        jobs = (await db.execute(select(SearchJob))).scalars().all()
        for job in jobs:
            job.status = JobStatus.PAUSED
            job.total_found = 0
            job.total_verified = 0
            job.last_error = None
            job.activity_message = "Reset done — Start / create will list+harvest"
            job.last_activity_at = datetime.utcnow()
            job.harvest_pid = None
            job.listing_pid = None
        await db.commit()
        print(f"Deleted leads={leads_n} company_checks={checks_n}")
        print(f"Reset company_queue rows to pending={queue_n}")
        print(f"Jobs kept={len(jobs)} (PAUSED, total_found=0)")


if __name__ == "__main__":
    asyncio.run(main())
