"""Persist checked companies so pause/restart never re-visits them."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company_check import CompanyCheck


def _is_news_or_blocked_domain(domain: str) -> bool:
    from app.services.scraper import SKIP_DOMAINS

    dom = domain.lower().replace("www.", "")
    return any(s in dom for s in SKIP_DOMAINS)


async def upsert_company_check(
    db: AsyncSession,
    *,
    domain: str | None,
    company_name: str | None,
    location: str | None = None,
    industry: str | None = None,
) -> None:
    if not domain:
        return
    domain = domain.lower().replace("www.", "").strip()
    if not domain or _is_news_or_blocked_domain(domain):
        return

    existing = await db.get(CompanyCheck, domain)
    if existing:
        if company_name and not existing.company_name:
            existing.company_name = company_name
        if location and not existing.location:
            existing.location = location
        if industry and not existing.industry:
            existing.industry = industry
        return

    db.add(
        CompanyCheck(
            domain=domain,
            company_name=company_name,
            location=location,
            industry=industry,
        )
    )


async def backfill_from_leads(db: AsyncSession) -> int:
    """Seed company_checks from all leads so restarts skip known domains immediately."""
    from app.models import Lead

    result = await db.execute(select(Lead.domain, Lead.company_name).distinct())
    n = 0
    for domain, company in result.all():
        if not domain:
            continue
        await upsert_company_check(db, domain=domain, company_name=company)
        n += 1
    if n:
        await db.commit()
    return n


async def cleanup_bad_company_checks(db: AsyncSession) -> int:
    """Delete company_checks pointing at news/media/blocked domains."""
    rows = (await db.execute(select(CompanyCheck))).scalars().all()
    deleted = 0
    for row in rows:
        dom = (row.domain or "").lower().replace("www.", "")
        if _is_news_or_blocked_domain(dom):
            await db.delete(row)
            deleted += 1
    if deleted:
        await db.commit()
    return deleted
