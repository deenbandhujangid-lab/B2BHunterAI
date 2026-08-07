"""Delete company_checks that point at news/media domains (bad website resolution).

Keeps checks that match a VALID lead domain. Safe to re-run.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import EmailStatus, Lead
from app.models.company_check import CompanyCheck
from app.services.scraper import SKIP_DOMAINS


async def main() -> None:
    async with AsyncSessionLocal() as db:
        lead_domains = {
            (d or "").lower().replace("www.", "")
            for (d,) in (
                await db.execute(
                    select(Lead.domain).where(Lead.email_status == EmailStatus.VALID)
                )
            ).all()
            if d
        }
        rows = (await db.execute(select(CompanyCheck))).scalars().all()
        deleted = 0
        kept = 0
        for row in rows:
            dom = (row.domain or "").lower().replace("www.", "")
            is_skip = any(s in dom for s in SKIP_DOMAINS)
            if dom in lead_domains and not is_skip:
                kept += 1
                continue
            # Drop news domains always; drop no-lead checks so they can be re-harvested
            if is_skip or dom not in lead_domains:
                await db.delete(row)
                deleted += 1
            else:
                kept += 1
        await db.commit()
        print(f"deleted={deleted} kept={kept}")


if __name__ == "__main__":
    asyncio.run(main())
