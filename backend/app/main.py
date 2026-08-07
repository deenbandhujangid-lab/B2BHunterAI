from __future__ import annotations

import asyncio
import csv
import io
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime

# Playwright on Windows requires ProactorEventLoop (subprocess support)
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db, init_db
from app.models import EmailStatus, JobStatus, Lead, SearchJob
from app.models.company_queue import CompanyQueue, QueueStatus
from app.models.search_query import QueryStatus, SearchQuery
from app.schemas import (
    CompanyQueueItem,
    CompanyQueueListResponse,
    DashboardStats,
    LeadListResponse,
    LeadResponse,
    SearchJobCreate,
    SearchJobListResponse,
    SearchJobResponse,
)
from app.services import job_controls
from app.services.company_checks import backfill_from_leads, cleanup_bad_company_checks
from app.services.company_queue import count_queue, reset_stale_harvesting
from app.services.job_log import job_log
from app.services.job_roles import format_job_roles
from app.services.query_bank import count_queries, ensure_queries_seeded
from app.services.workers import (
    kill_process,
    process_alive,
    spawn_harvest_exclusive,
    spawn_load,
)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _normalize_lead_statuses() -> None:
    """Hide legacy RISKY/UNVERIFIED from end users — promote UNVERIFIED, drop RISKY."""
    try:
        async with AsyncSessionLocal() as db:
            promoted = (
                await db.execute(
                    update(Lead)
                    .where(Lead.email_status == EmailStatus.UNVERIFIED)
                    .values(email_status=EmailStatus.VALID)
                )
            ).rowcount or 0
            removed = (
                await db.execute(
                    delete(Lead).where(Lead.email_status == EmailStatus.RISKY_CATCHALL)
                )
            ).rowcount or 0
            if promoted or removed:
                await db.commit()
                logger.info(
                    "Lead status cleanup: promoted %d UNVERIFIED → VALID, removed %d RISKY_CATCHALL",
                    promoted,
                    removed,
                )
    except Exception:
        logger.warning("Lead status cleanup skipped (DB busy)", exc_info=True)


async def _normalize_status_enums() -> None:
    """Force queue/query statuses to lowercase values (SQLAlchemy Enum name leak)."""
    from sqlalchemy import text

    try:
        async with AsyncSessionLocal() as db:
            pairs = [
                ("search_queries", "PENDING", "pending"),
                ("search_queries", "COMPLETED", "completed"),
                ("company_queue", "PENDING", "pending"),
                ("company_queue", "HARVESTING", "harvesting"),
                ("company_queue", "DONE", "done"),
                ("company_queue", "FAILED", "failed"),
            ]
            fixed = 0
            for table, old, new in pairs:
                r = await db.execute(
                    text(f"UPDATE {table} SET status=:new WHERE status=:old"),
                    {"new": new, "old": old},
                )
                fixed += r.rowcount or 0
            if fixed:
                await db.commit()
                logger.info("Normalized %d status enum rows to lowercase values", fixed)
    except Exception:
        logger.warning("Status enum normalize skipped (DB busy)", exc_info=True)


async def _resume_running_jobs() -> None:
    """After backend restart: leave harvest idle so API stays responsive."""
    await asyncio.sleep(0.3)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SearchJob).where(SearchJob.status == JobStatus.RUNNING)
        )
        jobs = result.scalars().all()
        for job in jobs:
            pending = await count_queue(
                db,
                location=job.target_location,
                industry=job.target_industry,
                status=QueueStatus.PENDING,
            )
            # Kill orphans left from previous API process — prevents fake "stuck" workers
            kill_process(job.harvest_pid)
            kill_process(job.listing_pid)
            job_controls.cancel_list(job.id)
            job_controls.request_pause(job.id)
            job_controls.mark_harvest_running(job.id, False)
            job.status = JobStatus.PAUSED
            job.harvest_pid = None
            job.listing_pid = None
            if pending > 0:
                job.activity_message = (
                    f"{pending} companies pending — click Start to resume harvest"
                )
            else:
                job.activity_message = (
                    "Ready — create/Start will list+harvest, or Load More Companies"
                )
            job.last_activity_at = datetime.utcnow()
            job.last_error = None
            await db.commit()
            logger.info(
                "Job %d left PAUSED after restart (%d pending companies)",
                job.id, pending,
            )


async def _job_stats_bulk(db: AsyncSession, jobs: list[SearchJob]) -> dict[tuple[str, str], dict]:
    """One-shot stats keyed by (location, industry) — keeps /api/jobs fast."""
    from sqlalchemy import case

    if not jobs:
        return {}

    pairs = {(j.target_location, j.target_industry) for j in jobs}
    # Company queue aggregates
    cq = await db.execute(
        select(
            CompanyQueue.location,
            CompanyQueue.industry,
            func.count(CompanyQueue.id),
            func.sum(case((CompanyQueue.status == QueueStatus.PENDING, 1), else_=0)),
            func.sum(case((CompanyQueue.status == QueueStatus.DONE, 1), else_=0)),
        ).group_by(CompanyQueue.location, CompanyQueue.industry)
    )
    out: dict[tuple[str, str], dict] = {
        p: {
            "companies_total": 0,
            "companies_pending": 0,
            "companies_done": 0,
            "queries_total": 0,
            "queries_pending": 0,
            "queries_completed": 0,
            "listing": False,
        }
        for p in pairs
    }
    for loc, ind, total, pending, done in cq.all():
        key = (loc, ind)
        if key in out:
            out[key]["companies_total"] = int(total or 0)
            out[key]["companies_pending"] = int(pending or 0)
            out[key]["companies_done"] = int(done or 0)

    sq = await db.execute(
        select(
            SearchQuery.location,
            SearchQuery.industry,
            func.count(SearchQuery.id),
            func.sum(case((SearchQuery.status == QueryStatus.PENDING, 1), else_=0)),
            func.sum(case((SearchQuery.status == QueryStatus.COMPLETED, 1), else_=0)),
        ).group_by(SearchQuery.location, SearchQuery.industry)
    )
    for loc, ind, total, pending, done in sq.all():
        key = (loc, ind)
        if key in out:
            out[key]["queries_total"] = int(total or 0)
            out[key]["queries_pending"] = int(pending or 0)
            out[key]["queries_completed"] = int(done or 0)

    return out


async def _job_stats(db: AsyncSession, job: SearchJob) -> dict:
    bulk = await _job_stats_bulk(db, [job])
    stats = bulk.get(
        (job.target_location, job.target_industry),
        {
            "companies_total": 0,
            "companies_pending": 0,
            "companies_done": 0,
            "queries_total": 0,
            "queries_pending": 0,
            "queries_completed": 0,
        },
    )
    listing = process_alive(job.listing_pid) or job_controls.is_list_running(job.id)
    harvesting = process_alive(job.harvest_pid) or (
        job.status == JobStatus.RUNNING and not listing
    )
    stats = {**stats, "listing": listing, "harvesting": harvesting}
    return stats


def _schedule_harvest(job_id: int, old_pid: int | None = None) -> int:
    """Spawn harvest in a separate process — Playwright must not share the API process."""
    return spawn_harvest_exclusive(job_id, old_pid)


def _schedule_load_companies(job_id: int) -> int:
    """Spawn company listing in a separate process."""
    return spawn_load(job_id)


async def _watchdog_loop() -> None:
    """Unstick dead workers / stale PIDs so UI never shows fake LOADING forever."""
    from datetime import timedelta

    await asyncio.sleep(20)
    while True:
        try:
            async with AsyncSessionLocal() as db:
                jobs = (await db.execute(select(SearchJob))).scalars().all()
                now = datetime.utcnow()
                for job in jobs:
                    changed = False
                    listing_alive = process_alive(job.listing_pid)
                    harvest_alive = process_alive(job.harvest_pid)

                    if job.listing_pid and not listing_alive:
                        job_log(job.id, "WATCHDOG", f"dead listing_pid={job.listing_pid}")
                        job.listing_pid = None
                        job_controls.clear_list(job.id)
                        changed = True
                    if job.harvest_pid and not harvest_alive:
                        job_log(job.id, "WATCHDOG", f"dead harvest_pid={job.harvest_pid}")
                        job.harvest_pid = None
                        job_controls.mark_harvest_running(job.id, False)
                        changed = True

                    stale = False
                    if job.last_activity_at:
                        age = now - job.last_activity_at
                        # Google gaps ~2min; 12min with no heartbeat = real hang
                        stale = age > timedelta(minutes=12)

                    if stale and (listing_alive or harvest_alive or job.status == JobStatus.RUNNING):
                        job_log(
                            job.id,
                            "WATCHDOG",
                            f"stale activity — killing workers age={job.last_activity_at}",
                        )
                        kill_process(job.harvest_pid)
                        kill_process(job.listing_pid)
                        job.harvest_pid = None
                        job.listing_pid = None
                        job_controls.cancel_list(job.id)
                        job_controls.request_pause(job.id)
                        job_controls.mark_harvest_running(job.id, False)
                        await reset_stale_harvesting(db)
                        pending = await count_queue(
                            db,
                            location=job.target_location,
                            industry=job.target_industry,
                            status=QueueStatus.PENDING,
                        )
                        job.status = JobStatus.PAUSED
                        job.activity_message = (
                            f"Auto-recovered from stall — {pending} pending. "
                            "Click Start (scrape) or Load More (list)."
                        )
                        job.last_activity_at = now
                        job.last_error = None
                        changed = True
                    elif changed and not listing_alive and not harvest_alive:
                        if job.status == JobStatus.RUNNING:
                            pending = await count_queue(
                                db,
                                location=job.target_location,
                                industry=job.target_industry,
                                status=QueueStatus.PENDING,
                            )
                            job.status = JobStatus.PAUSED
                            job.activity_message = (
                                f"Workers ended — {pending} pending. "
                                "Click Start or Load More."
                            )
                            job.last_activity_at = now

                    if changed:
                        await db.commit()
        except Exception:
            logger.warning("Watchdog tick failed", exc_info=True)
        await asyncio.sleep(45)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s on port %d", settings.APP_NAME, settings.APP_VERSION, settings.API_PORT)
    await init_db()
    await _normalize_lead_statuses()
    await _normalize_status_enums()
    async with AsyncSessionLocal() as db:
        removed = await cleanup_bad_company_checks(db)
        if removed:
            logger.info("Removed %d news/blocked company_checks", removed)
        n = await backfill_from_leads(db)
        if n:
            logger.info("company_checks backfill: %d domains from leads", n)
    asyncio.create_task(_resume_running_jobs())
    asyncio.create_task(_watchdog_loop())
    yield
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "port": settings.API_PORT}


@app.get("/api/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    total = (
        await db.execute(
            select(func.count(Lead.id)).where(Lead.email_status == EmailStatus.VALID)
        )
    ).scalar() or 0
    active = (
        await db.execute(
            select(func.count(SearchJob.id)).where(SearchJob.status == JobStatus.RUNNING)
        )
    ).scalar() or 0
    paused = (
        await db.execute(
            select(func.count(SearchJob.id)).where(SearchJob.status == JobStatus.PAUSED)
        )
    ).scalar() or 0
    jobs = (await db.execute(select(func.count(SearchJob.id)))).scalar() or 0
    return DashboardStats(
        total_extracted=total,
        active_jobs=active,
        total_jobs=jobs,
        paused_jobs=paused,
    )


def _job_response(job: SearchJob, stats: dict) -> SearchJobResponse:
    base = SearchJobResponse.model_validate(job)
    return base.model_copy(update=stats)


@app.post("/api/jobs", response_model=SearchJobResponse, status_code=201)
async def create_job(
    payload: SearchJobCreate,
    db: AsyncSession = Depends(get_db),
):
    roles = [r.value for r in payload.target_roles or []]
    roles_label = format_job_roles(roles)
    job_name = payload.job_name or (
        f"{payload.target_location} {payload.target_industry} {roles_label}"
    )
    job = SearchJob(
        job_name=job_name,
        target_location=payload.target_location,
        target_industry=payload.target_industry,
        target_role=roles_label,
        daily_target=payload.daily_target,
        status=JobStatus.RUNNING,
        restart_count=0,
        activity_message=(
            f"Starting — listing up to {settings.COMPANIES_PER_LOAD} new companies…"
        ),
        last_activity_at=datetime.utcnow(),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    await ensure_queries_seeded(db, job.target_location, job.target_industry)
    job_log(job.id, "JOB_CREATE", f"{job.target_location}/{job.target_industry}")

    # Auto list ~500 then harvest (subprocess — API stays free)
    pid = _schedule_load_companies(job.id)
    job.listing_pid = pid
    await db.commit()
    await db.refresh(job)
    job_log(job.id, "LIST_SPAWN", f"pid={pid} target={settings.COMPANIES_PER_LOAD}")

    stats = await _job_stats(db, job)
    return _job_response(job, stats)


@app.get("/api/jobs", response_model=SearchJobListResponse)
async def list_jobs(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(SearchJob.id)))).scalar() or 0
    result = await db.execute(
        select(SearchJob).order_by(SearchJob.created_at.desc()).limit(100)
    )
    jobs = list(result.scalars().all())
    bulk = await _job_stats_bulk(db, jobs)
    out = []
    for job in jobs:
        listing = process_alive(job.listing_pid) or job_controls.is_list_running(job.id)
        harvesting = process_alive(job.harvest_pid)
        stats = {
            **bulk.get(
                (job.target_location, job.target_industry),
                {
                    "companies_total": 0,
                    "companies_pending": 0,
                    "companies_done": 0,
                    "queries_total": 0,
                    "queries_pending": 0,
                    "queries_completed": 0,
                },
            ),
            "listing": listing,
            "harvesting": harvesting,
        }
        out.append(_job_response(job, stats))
    return SearchJobListResponse(jobs=out, total=total)


@app.get("/api/serpapi-cache/stats")
async def serpapi_cache_stats(db: AsyncSession = Depends(get_db)):
    """How many SerpAPI JSON responses are stored for free replay."""
    from app.models.serpapi_cache import SerpApiCache

    total = (await db.execute(select(func.count(SerpApiCache.id)))).scalar() or 0
    organics = (
        await db.execute(select(func.coalesce(func.sum(SerpApiCache.organic_count), 0)))
    ).scalar() or 0
    return {
        "cached_searches": total,
        "cached_organic_links": int(organics),
        "enabled": bool(getattr(settings, "SERPAPI_CACHE_ENABLED", True)),
        "ttl_days": int(getattr(settings, "SERPAPI_CACHE_TTL_DAYS", 0) or 0),
    }


@app.post("/api/jobs/{job_id}/requeue-cached-queries")
async def requeue_cached_queries(
    job_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Re-open completed queries that have SerpAPI JSON in DB → Load More uses 0 credits."""
    from app.models.serpapi_cache import SerpApiCache
    from app.services.serpapi_cache import normalize_query_key

    job = await _get_job(db, job_id)
    limit = max(1, min(int(limit or 50), 200))
    completed = list(
        (
            await db.execute(
                select(SearchQuery)
                .where(
                    SearchQuery.location == job.target_location,
                    SearchQuery.industry == job.target_industry,
                    SearchQuery.status == QueryStatus.COMPLETED,
                )
                .order_by(SearchQuery.id.desc())
                .limit(500)
            )
        ).scalars().all()
    )
    if not completed:
        return {"requeued": 0, "message": "No completed queries to requeue"}

    keys = {normalize_query_key(q.query) for q in completed}
    cached_keys = set(
        (
            await db.execute(
                select(SerpApiCache.query_key).where(SerpApiCache.query_key.in_(keys))
            )
        ).scalars().all()
    )
    requeued = 0
    for q in completed:
        if requeued >= limit:
            break
        if normalize_query_key(q.query) not in cached_keys:
            continue
        q.status = QueryStatus.PENDING
        q.completed_at = None
        requeued += 1
    if requeued:
        job.activity_message = (
            f"Requeued {requeued} cached SerpAPI queries (0 credits on Load More)"
        )
        job.last_activity_at = datetime.utcnow()
        await db.commit()
    return {
        "requeued": requeued,
        "cached_available": len(cached_keys),
        "message": (
            f"Requeued {requeued} queries with SerpAPI cache — click Load More (0 credits)"
            if requeued
            else "No completed queries have SerpAPI cache yet"
        ),
    }


@app.post("/api/jobs/{job_id}/pause", response_model=SearchJobResponse)
async def pause_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Full stop: harvest + Load More listing. Progress kept for later Start / Load More."""
    job = await _get_job(db, job_id)
    listing = process_alive(job.listing_pid) or job_controls.is_list_running(job_id)
    harvesting = process_alive(job.harvest_pid) or (
        job.status == JobStatus.RUNNING and not listing
    )
    if not harvesting and not listing and job.status != JobStatus.RUNNING:
        raise HTTPException(400, detail="Nothing running to stop")

    job_controls.request_pause(job_id)
    job_controls.hold_harvest(job_id)
    job_controls.cancel_list(job_id)
    kill_process(job.harvest_pid)
    kill_process(job.listing_pid)
    job.harvest_pid = None
    job.listing_pid = None
    job.status = JobStatus.PAUSED
    job.last_error = "Paused by user"
    job.activity_message = "Stopped — listing + harvest paused. Start = scrape, Load More = list companies"
    job.last_activity_at = datetime.utcnow()
    await db.commit()
    await db.refresh(job)
    job_log(job_id, "PAUSED", "user full stop")
    return _job_response(job, await _job_stats(db, job))


@app.post("/api/jobs/{job_id}/load-companies", response_model=SearchJobResponse)
async def load_companies(
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Load More: next ~COMPANIES_PER_LOAD new domains. Does NOT stop harvest."""
    job = await _get_job(db, job_id)
    if process_alive(job.listing_pid) or job_controls.is_list_running(job_id):
        raise HTTPException(400, detail="Company load already running for this job")

    await ensure_queries_seeded(db, job.target_location, job.target_industry)
    pending_q = await count_queries(
        db,
        location=job.target_location,
        industry=job.target_industry,
        status=QueryStatus.PENDING,
    )
    if pending_q <= 0:
        raise HTTPException(
            400,
            detail="No pending search queries left for this location/industry",
        )

    # Do not clear harvest hold — Start is required to scrape after Stop
    if job.status in (JobStatus.COMPLETED, JobStatus.PAUSED):
        job.status = JobStatus.RUNNING
    job.activity_message = (
        f"Load More — up to {settings.COMPANIES_PER_LOAD} new companies "
        f"({pending_q} queries left)…"
    )
    job.last_activity_at = datetime.utcnow()
    await db.commit()
    await db.refresh(job)

    pid = _schedule_load_companies(job.id)
    job.listing_pid = pid
    await db.commit()
    await db.refresh(job)
    job_log(job_id, "LIST_SPAWN", f"load_more pid={pid}")
    return _job_response(job, await _job_stats(db, job))


@app.post("/api/jobs/{job_id}/start", response_model=SearchJobResponse)
async def start_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Resume email harvest for pending companies."""
    job = await _get_job(db, job_id)
    if process_alive(job.harvest_pid):
        raise HTTPException(400, detail="Email harvest already running")

    await reset_stale_harvesting(db)
    pending = await count_queue(
        db,
        location=job.target_location,
        industry=job.target_industry,
        status=QueueStatus.PENDING,
    )
    if pending <= 0:
        # Auto-list first batch if nothing pending
        pending_q = await count_queries(
            db,
            location=job.target_location,
            industry=job.target_industry,
            status=QueryStatus.PENDING,
        )
        if pending_q > 0 and not process_alive(job.listing_pid):
            job_controls.clear_pause(job_id)
            job_controls.release_harvest_hold(job_id)
            job.status = JobStatus.RUNNING
            job.last_error = None
            job.activity_message = (
                f"Starting — listing up to {settings.COMPANIES_PER_LOAD} companies…"
            )
            job.last_activity_at = datetime.utcnow()
            pid = _schedule_load_companies(job.id)
            job.listing_pid = pid
            await db.commit()
            await db.refresh(job)
            return _job_response(job, await _job_stats(db, job))
        raise HTTPException(
            400,
            detail="No pending companies — click Load More Companies first",
        )

    job_controls.begin_run(job_id)
    job_controls.clear_pause(job_id)
    if not job.restart_count:
        job.restart_count = 1
    job.status = JobStatus.RUNNING
    job.last_error = None
    job.activity_message = f"Starting email harvest — {pending} companies pending…"
    job.last_activity_at = datetime.utcnow()
    pid = _schedule_harvest(job.id, job.harvest_pid)
    job.harvest_pid = pid
    await db.commit()
    await db.refresh(job)
    job_log(job_id, "HARVEST_SPAWN", f"start pid={pid}")
    return _job_response(job, await _job_stats(db, job))


@app.post("/api/jobs/{job_id}/restart", response_model=SearchJobResponse)
async def restart_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Restart email harvest for pending companies."""
    job = await _get_job(db, job_id)
    if process_alive(job.harvest_pid):
        raise HTTPException(400, detail="Email harvest already running")

    await reset_stale_harvesting(db)
    pending = await count_queue(
        db,
        location=job.target_location,
        industry=job.target_industry,
        status=QueueStatus.PENDING,
    )
    if pending <= 0:
        raise HTTPException(
            400,
            detail="No pending companies — click Load More Companies first",
        )

    job_controls.begin_run(job_id)
    job_controls.clear_pause(job_id)
    job.restart_count = (job.restart_count or 0) + 1
    job.status = JobStatus.RUNNING
    job.last_error = None
    job.activity_message = f"Restarting email harvest — {pending} companies pending…"
    job.last_activity_at = datetime.utcnow()
    pid = _schedule_harvest(job.id, job.harvest_pid)
    job.harvest_pid = pid
    await db.commit()
    await db.refresh(job)
    job_log(job_id, "HARVEST_SPAWN", f"restart pid={pid}")
    return _job_response(job, await _job_stats(db, job))


async def _get_job(db: AsyncSession, job_id: int) -> SearchJob:
    result = await db.execute(select(SearchJob).where(SearchJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/companies", response_model=CompanyQueueListResponse)
async def list_job_companies(
    job_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = Query(
        None, description="pending|harvesting|done|failed|scraped|all"
    ),
    q: str | None = Query(None, description="Search name or domain"),
    db: AsyncSession = Depends(get_db),
):
    """Companies listed for this job's location+industry, with scrape status."""
    job = await _get_job(db, job_id)
    base = select(CompanyQueue).where(
        CompanyQueue.location == job.target_location,
        CompanyQueue.industry == job.target_industry,
    )
    count_base = select(func.count(CompanyQueue.id)).where(
        CompanyQueue.location == job.target_location,
        CompanyQueue.industry == job.target_industry,
    )

    status_l = (status or "all").strip().lower()
    if status_l == "scraped":
        base = base.where(CompanyQueue.status.in_([QueueStatus.DONE, QueueStatus.FAILED]))
        count_base = count_base.where(
            CompanyQueue.status.in_([QueueStatus.DONE, QueueStatus.FAILED])
        )
    elif status_l in ("pending", "harvesting", "done", "failed"):
        st = QueueStatus(status_l)
        base = base.where(CompanyQueue.status == st)
        count_base = count_base.where(CompanyQueue.status == st)

    if q and q.strip():
        like = f"%{_escape_like(q.strip())}%"
        filt = (
            CompanyQueue.domain.ilike(like, escape="\\")
            | CompanyQueue.company_name.ilike(like, escape="\\")
        )
        base = base.where(filt)
        count_base = count_base.where(filt)

    total = (await db.execute(count_base)).scalar() or 0
    rows = (
        await db.execute(
            base.order_by(CompanyQueue.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    # Status counts for this location/industry (unfiltered by q)
    from sqlalchemy import case

    agg = await db.execute(
        select(
            func.sum(case((CompanyQueue.status == QueueStatus.PENDING, 1), else_=0)),
            func.sum(case((CompanyQueue.status == QueueStatus.HARVESTING, 1), else_=0)),
            func.sum(case((CompanyQueue.status == QueueStatus.DONE, 1), else_=0)),
            func.sum(case((CompanyQueue.status == QueueStatus.FAILED, 1), else_=0)),
        ).where(
            CompanyQueue.location == job.target_location,
            CompanyQueue.industry == job.target_industry,
        )
    )
    pending_n, harvesting_n, done_n, failed_n = agg.one()

    items = [
        CompanyQueueItem(
            id=r.id,
            domain=r.domain,
            company_name=r.company_name,
            website=r.website,
            location=r.location,
            industry=r.industry,
            source=r.source or "search",
            size_label=getattr(r, "size_label", None),
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            last_error=r.last_error,
            created_at=r.created_at,
            checked_at=r.checked_at,
            scraped=r.status in (QueueStatus.DONE, QueueStatus.FAILED),
        )
        for r in rows
    ]
    return CompanyQueueListResponse(
        companies=items,
        total=total,
        page=page,
        page_size=page_size,
        pending=int(pending_n or 0),
        harvesting=int(harvesting_n or 0),
        done=int(done_n or 0),
        failed=int(failed_n or 0),
        job_id=job.id,
        job_name=job.job_name,
        location=job.target_location,
        industry=job.target_industry,
    )


def _parse_date_param(value: str | None, label: str) -> str | None:
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(400, detail=f"Invalid {label}; use YYYY-MM-DD") from exc
    return value


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_lead_filters(
    query,
    count_q,
    *,
    job_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    role: str | None = None,
    location: str | None = None,
    industry: str | None = None,
    company: str | None = None,
    email: str | None = None,
):
    query = query.where(Lead.email_status == EmailStatus.VALID)
    count_q = count_q.where(Lead.email_status == EmailStatus.VALID)

    if location or industry:
        query = query.join(SearchJob, Lead.job_id == SearchJob.id)
        count_q = count_q.join(SearchJob, Lead.job_id == SearchJob.id)

    if job_id:
        query = query.where(Lead.job_id == job_id)
        count_q = count_q.where(Lead.job_id == job_id)
    if date_from:
        query = query.where(func.date(Lead.created_at) >= date_from)
        count_q = count_q.where(func.date(Lead.created_at) >= date_from)
    if date_to:
        query = query.where(func.date(Lead.created_at) <= date_to)
        count_q = count_q.where(func.date(Lead.created_at) <= date_to)
    if role:
        query = query.where(Lead.role_category == role)
        count_q = count_q.where(Lead.role_category == role)
    if location:
        loc = _escape_like(location.strip())
        query = query.where(SearchJob.target_location.ilike(f"%{loc}%"))
        count_q = count_q.where(SearchJob.target_location.ilike(f"%{loc}%"))
    if industry:
        ind = _escape_like(industry.strip())
        query = query.where(SearchJob.target_industry.ilike(f"%{ind}%"))
        count_q = count_q.where(SearchJob.target_industry.ilike(f"%{ind}%"))
    if company:
        co = _escape_like(company.strip())
        query = query.where(Lead.company_name.ilike(f"%{co}%"))
        count_q = count_q.where(Lead.company_name.ilike(f"%{co}%"))
    if email:
        em = _escape_like(email.strip())
        query = query.where(Lead.email.ilike(f"%{em}%"))
        count_q = count_q.where(Lead.email.ilike(f"%{em}%"))
    return query, count_q


@app.get("/api/leads/filter-options")
async def lead_filter_options(db: AsyncSession = Depends(get_db)):
    """Distinct locations & industries from jobs that have verified leads."""
    loc_rows = await db.execute(
        select(SearchJob.target_location)
        .join(Lead, Lead.job_id == SearchJob.id)
        .where(Lead.email_status == EmailStatus.VALID)
        .distinct()
        .order_by(SearchJob.target_location)
    )
    ind_rows = await db.execute(
        select(SearchJob.target_industry)
        .join(Lead, Lead.job_id == SearchJob.id)
        .where(Lead.email_status == EmailStatus.VALID)
        .distinct()
        .order_by(SearchJob.target_industry)
    )
    return {
        "locations": [r[0] for r in loc_rows.all() if r[0]],
        "industries": [r[0] for r in ind_rows.all() if r[0]],
    }


@app.get("/api/leads", response_model=LeadListResponse)
async def list_leads(
    job_id: int | None = None,
    date_from: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    date_to: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    role: str | None = Query(None, description="Founder, HR, Admin, CMO"),
    location: str | None = Query(None, description="Filter by target location"),
    industry: str | None = Query(None, description="Filter by target industry"),
    company: str | None = Query(None, description="Filter by company name (contains)"),
    email: str | None = Query(None, description="Filter by email (contains)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    date_from = _parse_date_param(date_from, "date_from")
    date_to = _parse_date_param(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, detail="date_from must be on or before date_to")

    query, count_q = _apply_lead_filters(
        select(Lead),
        select(func.count(Lead.id)).select_from(Lead),
        job_id=job_id,
        date_from=date_from,
        date_to=date_to,
        role=role,
        location=location,
        industry=industry,
        company=company,
        email=email,
    )

    total = (await db.execute(count_q)).scalar() or 0
    result = await db.execute(
        query.options(selectinload(Lead.job))
        .order_by(Lead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    leads = result.scalars().all()
    return LeadListResponse(
        leads=[
            LeadResponse.model_validate(l).model_copy(
                update={
                    "target_location": l.job.target_location if l.job else None,
                    "target_industry": l.job.target_industry if l.job else None,
                }
            )
            for l in leads
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/api/leads/export")
async def export_leads(
    job_id: int | None = None,
    date_from: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    date_to: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    role: str | None = Query(None, description="Founder, HR, Admin, CMO"),
    location: str | None = Query(None, description="Filter by target location"),
    industry: str | None = Query(None, description="Filter by target industry"),
    company: str | None = Query(None, description="Filter by company name (contains)"),
    email: str | None = Query(None, description="Filter by email (contains)"),
    db: AsyncSession = Depends(get_db),
):
    date_from = _parse_date_param(date_from, "date_from")
    date_to = _parse_date_param(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, detail="date_from must be on or before date_to")

    query, _ = _apply_lead_filters(
        select(Lead),
        select(func.count(Lead.id)).select_from(Lead),
        job_id=job_id,
        date_from=date_from,
        date_to=date_to,
        role=role,
        location=location,
        industry=industry,
        company=company,
        email=email,
    )
    result = await db.execute(
        query.options(selectinload(Lead.job)).order_by(Lead.created_at.desc())
    )
    leads = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "target_location", "target_industry", "first_name", "last_name", "job_title",
        "company_name", "domain", "website", "email", "phone_e164",
        "source_url", "source_query", "linkedin_url",
    ])
    # Prefetch queue meta by domain
    from app.models.company_queue import CompanyQueue
    queue_rows = (await db.execute(select(CompanyQueue))).scalars().all()
    qmeta = {
        (r.domain or "").lower(): r for r in queue_rows
    }
    for l in leads:
        meta = qmeta.get((l.domain or "").lower())
        writer.writerow([
            l.job.target_location if l.job else "",
            l.job.target_industry if l.job else "",
            l.first_name, l.last_name, l.job_title, l.company_name, l.domain,
            (meta.website if meta else "") or (f"https://{l.domain}/" if l.domain else ""),
            l.email, l.phone_e164, l.source_url,
            meta.source_query if meta else "",
            meta.linkedin_url if meta else "",
        ])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads_export.csv"},
    )


@app.get("/api/companies/export")
async def export_company_universe(
    location: str | None = Query(None, description="Filter by city/location"),
    industry: str | None = Query(None, description="Filter by industry"),
    db: AsyncSession = Depends(get_db),
):
    """CSV: company universe + best email/phone from leads when available."""
    from app.models.company_queue import CompanyQueue

    q = select(CompanyQueue).order_by(CompanyQueue.id.asc())
    if location:
        q = q.where(CompanyQueue.location == location)
    if industry:
        q = q.where(CompanyQueue.industry == industry)
    rows = (await db.execute(q)).scalars().all()

    # Best lead per domain (first VALID)
    lead_q = select(Lead).where(Lead.email_status == EmailStatus.VALID)
    if location or industry:
        lead_q = lead_q.join(SearchJob, Lead.job_id == SearchJob.id)
        if location:
            lead_q = lead_q.where(SearchJob.target_location == location)
        if industry:
            lead_q = lead_q.where(SearchJob.target_industry == industry)
    leads = (await db.execute(lead_q.order_by(Lead.id.asc()))).scalars().all()
    by_domain: dict[str, Lead] = {}
    for lead in leads:
        d = (lead.domain or "").lower().replace("www.", "")
        if d and d not in by_domain:
            by_domain[d] = lead

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "company_name", "domain", "website", "city", "industry",
        "source_query", "source_url", "linkedin_url", "email", "phone", "status",
    ])
    for row in rows:
        d = (row.domain or "").lower()
        lead = by_domain.get(d)
        writer.writerow([
            row.company_name or "",
            row.domain or "",
            row.website or (f"https://{row.domain}/" if row.domain else ""),
            row.location or "",
            row.industry or "",
            row.source_query or "",
            row.source_url or "",
            row.linkedin_url or "",
            lead.email if lead else "",
            (lead.phone_e164 or lead.phone_raw or "") if lead else "",
            row.status.value if row.status else "",
        ])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=companies_export.csv"},
    )
