"""Stop duplicate harvest/API processes and reset stuck harvesting rows."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select, update

from app.core.database import AsyncSessionLocal, init_db
from app.models import JobStatus, SearchJob
from app.models.company_check import CompanyCheck
from app.models.company_queue import QueueStatus
from app.services.company_queue import reset_stale_harvesting


def _kill_matching() -> None:
    ps = r"""
Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -match 'python' -and
    $_.CommandLine -match 'harvest_worker|load_worker|B2BHunterAI\\backend.*run\.py'
  } |
  ForEach-Object {
    Write-Host ("KILL " + $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=False,
    )


async def _reset_db() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        n = await reset_stale_harvesting(db)
        await db.execute(delete(CompanyCheck))
        jobs = (await db.execute(select(SearchJob))).scalars().all()
        for job in jobs:
            job.status = JobStatus.PAUSED
            job.harvest_pid = None
            job.listing_pid = None
            job.last_error = None
            job.activity_message = "Fixed harvest — click Start for leads"
        await db.commit()
        print(f"reset_harvesting={n} jobs={len(jobs)} company_checks cleared")


def main() -> None:
    _kill_matching()
    asyncio.run(_reset_db())
    print("Done. Start backend with start-backend.bat then click Start on the job.")


if __name__ == "__main__":
    main()
