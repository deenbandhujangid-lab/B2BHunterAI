"""CRUD helpers for company_queue (Phase 1 list / Phase 2 harvest)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company_queue import CompanyQueue, QueueStatus
from app.services.query_bank import infer_size_label
from app.services.scraper import is_blocked_host, registrable_domain


def _normalize_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    d = registrable_domain(domain)
    if not d or "." not in d:
        return None
    # Universities / schools — not B2B company targets
    if d.endswith(".edu") or d.endswith(".edu.cn") or d.endswith(".ac.in"):
        return None
    if is_blocked_host(d):
        return None
    return d


async def enqueue_company(
    db: AsyncSession,
    *,
    domain: str | None,
    company_name: str | None,
    location: str | None,
    industry: str | None,
    source: str,
    job_id: int | None = None,
    website: str | None = None,
    source_query: str | None = None,
    source_url: str | None = None,
    linkedin_url: str | None = None,
    size_label: str | None = None,
) -> bool:
    """Insert pending company if domain is new. Returns True if inserted."""
    domain = _normalize_domain(domain)
    if not domain:
        return False

    existing = (
        await db.execute(select(CompanyQueue.id).where(CompanyQueue.domain == domain))
    ).scalar_one_or_none()
    if existing:
        return False

    site = website or f"https://{domain}/"
    label = size_label or infer_size_label(source_query)
    try:
        async with db.begin_nested():
            db.add(
                CompanyQueue(
                    domain=domain,
                    company_name=(company_name or domain)[:255],
                    website=(site or "")[:500] or None,
                    location=location,
                    industry=industry,
                    source=source[:50],
                    source_query=(source_query or "")[:500] or None,
                    source_url=(source_url or "")[:1000] or None,
                    linkedin_url=(linkedin_url or "")[:500] or None,
                    size_label=(label or "")[:20] or None,
                    job_id=job_id,
                    status=QueueStatus.PENDING,
                )
            )
            await db.flush()
        return True
    except Exception:
        return False


async def count_queue(
    db: AsyncSession,
    *,
    location: str | None = None,
    industry: str | None = None,
    status: QueueStatus | None = None,
) -> int:
    q = select(func.count(CompanyQueue.id))
    if location:
        q = q.where(CompanyQueue.location == location)
    if industry:
        q = q.where(CompanyQueue.industry == industry)
    if status is not None:
        q = q.where(CompanyQueue.status == status)
    return (await db.execute(q)).scalar() or 0


async def fetch_pending(
    db: AsyncSession,
    *,
    location: str,
    industry: str,
    limit: int = 50,
) -> list[CompanyQueue]:
    result = await db.execute(
        select(CompanyQueue)
        .where(
            CompanyQueue.status == QueueStatus.PENDING,
            CompanyQueue.location == location,
            CompanyQueue.industry == industry,
        )
        .order_by(CompanyQueue.id.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def mark_harvesting(db: AsyncSession, queue_id: int) -> None:
    await db.execute(
        update(CompanyQueue)
        .where(CompanyQueue.id == queue_id)
        .values(status=QueueStatus.HARVESTING)
    )


async def mark_done(db: AsyncSession, queue_id: int) -> None:
    await db.execute(
        update(CompanyQueue)
        .where(CompanyQueue.id == queue_id)
        .values(status=QueueStatus.DONE, checked_at=datetime.utcnow(), last_error=None)
    )


async def mark_failed(db: AsyncSession, queue_id: int, error: str) -> None:
    await db.execute(
        update(CompanyQueue)
        .where(CompanyQueue.id == queue_id)
        .values(
            status=QueueStatus.FAILED,
            checked_at=datetime.utcnow(),
            last_error=(error or "")[:500],
        )
    )


async def reset_stale_harvesting(db: AsyncSession) -> int:
    """On restart, harvesting rows become pending again."""
    result = await db.execute(
        update(CompanyQueue)
        .where(CompanyQueue.status == QueueStatus.HARVESTING)
        .values(status=QueueStatus.PENDING)
    )
    return result.rowcount or 0
