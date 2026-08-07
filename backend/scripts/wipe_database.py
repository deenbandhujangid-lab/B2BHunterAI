"""Delete all leads and search jobs — fresh start."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as db:
        leads_before = (await db.execute(text("SELECT COUNT(*) FROM leads"))).scalar() or 0
        jobs_before = (await db.execute(text("SELECT COUNT(*) FROM search_jobs"))).scalar() or 0
        print(f"Before: {leads_before} leads, {jobs_before} jobs")

        await db.execute(text("DELETE FROM leads"))
        await db.execute(text("DELETE FROM search_jobs"))
        try:
            await db.execute(
                text("DELETE FROM sqlite_sequence WHERE name IN ('leads', 'search_jobs')")
            )
        except Exception:
            pass
        await db.commit()

        leads_after = (await db.execute(text("SELECT COUNT(*) FROM leads"))).scalar() or 0
        jobs_after = (await db.execute(text("SELECT COUNT(*) FROM search_jobs"))).scalar() or 0
        print(f"After: {leads_after} leads, {jobs_after} jobs")
        print("Database wiped successfully.")


if __name__ == "__main__":
    asyncio.run(main())
