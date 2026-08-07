from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import EmailStatus, JobStatus, Lead, SearchJob
from app.models.company_queue import CompanyQueue, QueueStatus
from app.models.search_query import QueryStatus
from app.services import job_controls
from app.services.company_checks import upsert_company_check
from app.services.company_lister import CompanyLister, ListedCompany
from app.services.company_queue import (
    count_queue,
    enqueue_company,
    fetch_pending,
    mark_done,
    mark_failed,
    mark_harvesting,
    reset_stale_harvesting,
)
from app.services.data_quality import is_scraped_email_acceptable, sanitize_person_name
from app.services.email_harvester import EmailHarvester
from app.services.job_roles import parse_job_roles
from app.services.query_bank import (
    count_queries,
    ensure_queries_seeded,
    fetch_pending_queries,
    mark_query_completed,
)
from app.services.job_log import job_log
from app.services.run_session import load_run_session_from_db
from app.services.scraper import ScrapedContact
from app.services.verifier import EmailVerifier
from app.services.workers import process_alive, spawn_harvest_exclusive

logger = logging.getLogger(__name__)


async def run_local_scraping_job(job_id: int) -> None:
    """Backward-compat: email harvest only (companies load is manual)."""
    await run_harvest_emails_job(job_id)


async def run_load_companies_job(job_id: int) -> None:
    """Manual Phase 1 — query-bank list only. Then kicks off harvest."""
    await asyncio.to_thread(_run_load_companies_blocking, job_id)


async def run_harvest_emails_job(job_id: int) -> None:
    """Auto Phase 2 — harvest emails from pending company_queue."""
    await asyncio.to_thread(_run_harvest_blocking, job_id)


def _run_load_companies_blocking(job_id: int) -> None:
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_load_companies_async(job_id))
    finally:
        loop.close()


def _run_harvest_blocking(job_id: int) -> None:
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_harvest_async(job_id))
    finally:
        loop.close()


async def _run_load_companies_async(job_id: int) -> None:
    """List up to COMPANIES_PER_LOAD new domains. Does not pause harvest."""
    import os

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SearchJob).where(SearchJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            return

        # Listing-first: do not auto-scrape until user clicks Start
        if settings.LIST_FIRST_HOLD_HARVEST:
            job_controls.hold_harvest(job_id)

        list_token = job_controls.begin_list(job_id)
        job.listing_pid = os.getpid()
        # Listing active — never leave UI stuck on PAUSED while worker runs
        if job.status != JobStatus.COMPLETED:
            job.status = JobStatus.RUNNING
        await ensure_queries_seeded(db, job.target_location, job.target_industry)

        total_q = await count_queries(
            db, location=job.target_location, industry=job.target_industry
        )
        done_q = await count_queries(
            db,
            location=job.target_location,
            industry=job.target_industry,
            status=QueryStatus.COMPLETED,
        )
        target_new = settings.COMPANIES_PER_LOAD
        inserted_new = 0
        hit_target = False
        last_harvest_kick_at = 0

        job.activity_message = (
            f"Loading up to {target_new} new companies — Queries {done_q}/{total_q}…"
        )
        job.last_activity_at = datetime.utcnow()
        # Keep "Paused by user" if harvest is held; otherwise clear transient errors
        if not job_controls.is_harvest_held(job_id):
            job.last_error = None
        await db.commit()
        job_log(job_id, "LIST_START", f"target_new={target_new} queries={done_q}/{total_q}")

        pending_qs = await fetch_pending_queries(
            db,
            location=job.target_location,
            industry=job.target_industry,
            limit=settings.QUERIES_PER_RUN,
        )

        def should_stop() -> bool:
            return hit_target or job_controls.should_stop_list(job_id, list_token)

        async def touch(msg: str) -> None:
            if job_controls.should_stop_list(job_id, list_token):
                return
            job.activity_message = msg[:500]
            job.last_activity_at = datetime.utcnow()
            await db.commit()

        if not pending_qs:
            listed = await count_queue(
                db, location=job.target_location, industry=job.target_industry
            )
            await touch(
                f"No pending queries — {listed} companies already listed."
            )
            job_log(job_id, "LIST_EMPTY", f"listed={listed}")
            job.listing_pid = None
            await db.commit()
            job_controls.clear_list(job_id)
            await _kick_harvest_if_needed(job_id)
            return

        lister = CompanyLister()
        skip_domains: set[str] = set(
            (await db.execute(select(CompanyQueue.domain))).scalars().all()
        )
        skip_domains = {d.lower() for d in skip_domains if d}

        async def on_company(item: ListedCompany) -> None:
            nonlocal inserted_new, hit_target, last_harvest_kick_at
            if should_stop():
                return
            inserted = await enqueue_company(
                db,
                domain=item.domain,
                company_name=item.company_name,
                location=job.target_location,
                industry=job.target_industry,
                source=item.source,
                job_id=job.id,
                website=item.website,
                source_query=item.source_query,
                source_url=item.source_url,
                linkedin_url=item.linkedin_url,
            )
            if inserted:
                inserted_new += 1
                await db.commit()
                job_log(job_id, "COMPANY_ENQUEUED", item.domain)
                total = await count_queue(
                    db, location=job.target_location, industry=job.target_industry
                )
                completed = await count_queries(
                    db,
                    location=job.target_location,
                    industry=job.target_industry,
                    status=QueryStatus.COMPLETED,
                )
                await touch(
                    f"New {inserted_new}/{target_new} — Queries {completed}/{total_q} "
                    f"— listed {total} — last: {item.domain}"
                )
                if inserted_new >= target_new:
                    hit_target = True
                    job_log(job_id, "LIST_TARGET", f"reached {target_new} new companies")
                # Scrape in parallel while listing (unless user Stop held harvest)
                if (
                    inserted_new - last_harvest_kick_at >= 8
                    and not job_controls.is_harvest_held(job_id)
                ):
                    last_harvest_kick_at = inserted_new
                    await _kick_harvest_if_needed(job_id)

        async def on_query_done(query_id: int) -> None:
            if job_controls.should_stop_list(job_id, list_token):
                return
            await mark_query_completed(db, query_id)
            await db.commit()
            job_log(job_id, "LIST_QUERY_DONE", f"query_id={query_id}")
            completed = await count_queries(
                db,
                location=job.target_location,
                industry=job.target_industry,
                status=QueryStatus.COMPLETED,
            )
            listed = await count_queue(
                db, location=job.target_location, industry=job.target_industry
            )
            await touch(
                f"New {inserted_new}/{target_new} — Queries {completed}/{total_q} "
                f"— listed {listed}"
            )

        try:
            batch_n = 0
            while pending_qs and not should_stop():
                batch_n += 1
                # Refresh total after mid-run seeding
                total_q = await count_queries(
                    db, location=job.target_location, industry=job.target_industry
                )
                job_log(job_id, "LIST_QUERY_START", f"batch={len(pending_qs)} round={batch_n}")
                await touch(
                    f"Loading companies — round {batch_n}, {len(pending_qs)} queries "
                    f"(target {target_new} new, have {inserted_new})…"
                )
                await lister.list_from_query_bank(
                    queries=[
                        {
                            "id": q.id,
                            "query": q.query,
                            "location": q.location or job.target_location,
                        }
                        for q in pending_qs
                    ],
                    on_company=on_company,
                    on_query_done=on_query_done,
                    on_activity=touch,
                    on_notice=touch,
                    should_stop=should_stop,
                    skip_domains=skip_domains,
                )
                if should_stop():
                    break
                skip_domains = {
                    d.lower()
                    for d in (await db.execute(select(CompanyQueue.domain))).scalars().all()
                    if d
                }
                # Between query rounds, keep scrape moving on pending domains
                if not job_controls.is_harvest_held(job_id):
                    await _kick_harvest_if_needed(job_id)
                pending_qs = await fetch_pending_queries(
                    db,
                    location=job.target_location,
                    industry=job.target_industry,
                    limit=settings.QUERIES_PER_RUN,
                )

            if (
                inserted_new < 20
                and not should_stop()
                and not (
                    settings.LISTING_SERPAPI_ONLY
                    and (settings.SERPAPI_KEY or "").strip()
                )
            ):
                skip_domains = {
                    d.lower()
                    for d in (await db.execute(select(CompanyQueue.domain))).scalars().all()
                    if d
                }
                await lister.list_companies(
                    location=job.target_location,
                    industry=job.target_industry,
                    role=parse_job_roles(job.target_role)[0],
                    list_target=min(40, target_new - inserted_new),
                    on_company=on_company,
                    on_activity=touch,
                    on_notice=touch,
                    should_stop=should_stop,
                    skip_domains=skip_domains,
                )

            listed = await count_queue(
                db, location=job.target_location, industry=job.target_industry
            )
            pending = await count_queue(
                db,
                location=job.target_location,
                industry=job.target_industry,
                status=QueueStatus.PENDING,
            )
            completed = await count_queries(
                db,
                location=job.target_location,
                industry=job.target_industry,
                status=QueryStatus.COMPLETED,
            )
            remaining_q = await count_queries(
                db,
                location=job.target_location,
                industry=job.target_industry,
                status=QueryStatus.PENDING,
            )
            await touch(
                f"Load done — +{inserted_new} new (target {target_new}). "
                f"Queries {completed}/{total_q}, listed {listed}, "
                f"{pending} pending harvest"
                + (f". {remaining_q} queries left." if remaining_q else ". All queries done.")
            )
            job_log(
                job_id,
                "LIST_DONE",
                f"new={inserted_new} listed={listed} pending={pending}",
            )
        except Exception as e:
            logger.exception("Load companies failed job %d", job_id)
            job.last_error = str(e)[:500]
            job.activity_message = f"Load companies error: {str(e)[:200]}"
            job.last_activity_at = datetime.utcnow()
            await db.commit()
            job_log(job_id, "LIST_ERROR", str(e)[:200])
        finally:
            job.listing_pid = None
            await db.commit()
            job_controls.clear_list(job_id)

        await _kick_harvest_if_needed(job_id)


async def _kick_harvest_if_needed(job_id: int) -> None:
    """Start harvest subprocess if pending companies and harvest not already running."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SearchJob).where(SearchJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            return
        if process_alive(job.harvest_pid):
            job_log(job_id, "HARVEST_SKIP", f"already running pid={job.harvest_pid}")
            return
        # Explicit Stop → only Start may scrape again (Load More must not auto-start)
        if job_controls.is_harvest_held(job_id):
            job_log(job_id, "HARVEST_SKIP", "held until Start")
            return
        if job.status == JobStatus.PAUSED and (job.last_error or "") == "Paused by user":
            job_log(job_id, "HARVEST_SKIP", "job paused by user")
            return
        pending = await count_queue(
            db,
            location=job.target_location,
            industry=job.target_industry,
            status=QueueStatus.PENDING,
        )
        if pending <= 0:
            return

        job.status = JobStatus.RUNNING
        job.last_error = None
        job.activity_message = f"Auto-harvesting emails — {pending} companies pending…"
        job.last_activity_at = datetime.utcnow()
        if not job.restart_count:
            job.restart_count = 1
        old_pid = job.harvest_pid
        pid = spawn_harvest_exclusive(job_id, old_pid)
        job.harvest_pid = pid
        await db.commit()
        job_log(job_id, "HARVEST_SPAWN", f"pid={pid} pending={pending}")


async def _run_harvest_async(job_id: int) -> None:
    """Harvest emails from pending company_queue until cap or empty."""
    import os

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SearchJob).where(SearchJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            logger.error("Job %d not found", job_id)
            return

        run_token = job_controls.get_run_token(job_id)
        if not run_token:
            run_token = job_controls.begin_run(job_id)

        job_controls.mark_harvest_running(job_id, True)
        session = await load_run_session_from_db(db)
        job_controls.set_run_session(job_id, session)

        starting_found = (
            await db.execute(
                select(func.count(Lead.id)).where(
                    Lead.job_id == job.id,
                    Lead.email_status == EmailStatus.VALID,
                )
            )
        ).scalar() or 0
        job.total_found = starting_found

        reset_n = await reset_stale_harvesting(db)
        if reset_n:
            logger.info("Job %d: reset %d stale harvesting → pending", job_id, reset_n)

        job.status = JobStatus.RUNNING
        job.harvest_pid = os.getpid()
        job.last_error = None
        pending_start = await count_queue(
            db,
            location=job.target_location,
            industry=job.target_industry,
            status=QueueStatus.PENDING,
        )
        job.activity_message = (
            f"Harvesting emails — {pending_start} companies pending "
            f"(skip {len(session.domains)} known domains)"
        )
        job.last_activity_at = datetime.utcnow()
        await db.commit()
        job_log(job_id, "HARVEST_START", f"pending={pending_start} pid={os.getpid()}")

        if pending_start == 0:
            remaining_q = await count_queries(
                db,
                location=job.target_location,
                industry=job.target_industry,
                status=QueryStatus.PENDING,
            )
            if remaining_q <= 0:
                job.status = JobStatus.COMPLETED
                job.activity_message = "COMPLETED — no pending companies or queries"
                job_log(job_id, "COMPLETED", "empty queue + no queries")
            else:
                job.status = JobStatus.PAUSED
                job.activity_message = (
                    "No pending companies — click Load More Companies"
                )
                job_log(job_id, "PAUSED", "empty pending queue")
            job.harvest_pid = None
            job.last_activity_at = datetime.utcnow()
            await db.commit()
            job_controls.mark_harvest_running(job_id, False)
            job_controls.clear_run_session(job_id)
            return

        verifier = EmailVerifier()
        harvester = EmailHarvester()
        saved_count = 0
        skipped_dupes = 0
        harvested_domains = 0
        roles = parse_job_roles(job.target_role)
        current_role = roles[0]
        cap = min(job.daily_target, settings.MAX_LEADS_PER_JOB)
        stop_flag = False

        async def refresh_stop_flag() -> None:
            """Cross-process pause: API sets job.status=PAUSED in DB."""
            nonlocal stop_flag
            if starting_found + saved_count >= cap:
                stop_flag = True
                return
            await db.refresh(job)
            if job.status == JobStatus.PAUSED or (job.last_error or "") == "Paused by user":
                stop_flag = True
                return
            stop_flag = False

        def should_stop() -> bool:
            return stop_flag or job_controls.should_stop(job_id, run_token)

        def is_stale_run() -> bool:
            return False

        def should_stop_scrape() -> bool:
            if should_stop():
                return True
            return starting_found + saved_count >= cap

        async def touch_activity(message: str) -> None:
            await refresh_stop_flag()
            if should_stop() or is_stale_run():
                return
            job.activity_message = message[:500]
            job.last_activity_at = datetime.utcnow()
            await db.commit()

        async def save_contact(contact: ScrapedContact) -> None:
            nonlocal saved_count, skipped_dupes, current_role
            if should_stop() or is_stale_run():
                return

            saved_any = False
            emails_saved = 0
            for email in contact.emails:
                email_l = email.lower().strip()
                if not is_scraped_email_acceptable(email_l):
                    continue
                if session.has_email(email_l):
                    skipped_dupes += 1
                    continue
                verify = await verifier.verify(email_l, contact.domain, quick=True)
                # Accept MX-ok UNVERIFIED as saveable; only drop clear INVALID/RISKY
                if verify.status in ("INVALID", "RISKY_CATCHALL"):
                    job_log(job_id, "LEAD_REJECT", f"{email_l}: {verify.status} {verify.detail}")
                    continue

                first_name, last_name = sanitize_person_name(
                    contact.first_name, contact.last_name
                )
                lead = Lead(
                    job_id=job.id,
                    first_name=first_name,
                    last_name=last_name,
                    job_title=contact.job_title,
                    role_category=contact.role_category or current_role,
                    company_name=contact.company_name,
                    domain=contact.domain,
                    email=email_l,
                    email_status=EmailStatus.VALID,
                    phone_raw=contact.phone_raw,
                    phone_e164=contact.phone_e164,
                    is_phone_valid=contact.is_phone_valid,
                    source_url=contact.source_url,
                )
                try:
                    async with db.begin_nested():
                        db.add(lead)
                        await db.flush()
                    session.add_lead(email_l, contact.domain, contact.company_name)
                    job_controls.set_run_session(job_id, session)
                    saved_count += 1
                    emails_saved += 1
                    saved_any = True
                    job_log(job_id, "LEAD_SAVED", email_l)
                except IntegrityError:
                    session.add_lead(email_l, contact.domain, contact.company_name)
                    job_controls.set_run_session(job_id, session)
                    skipped_dupes += 1

            if is_stale_run():
                return

            job.total_found = starting_found + saved_count
            listed_total = await count_queue(
                db, location=job.target_location, industry=job.target_industry
            )
            label = contact.domain or contact.company_name or "company"
            if saved_any:
                job.activity_message = (
                    f"Harvesting {harvested_domains}/{listed_total} — "
                    f"last: {label} — saved {emails_saved} "
                    f"(total {job.total_found})"
                )[:500]
            else:
                job.activity_message = (
                    f"Harvesting {harvested_domains}/{listed_total} — "
                    f"last: {label} — no new email"
                )[:500]
            job.last_activity_at = datetime.utcnow()
            await db.commit()

        async def on_activity(message: str) -> None:
            await touch_activity(message)

        terminal_status: JobStatus | None = None
        terminal_message: str | None = None
        terminal_error: str | None = None

        try:
            while True:
                await refresh_stop_flag()
                if should_stop_scrape():
                    break
                batch = await fetch_pending(
                    db,
                    location=job.target_location,
                    industry=job.target_industry,
                    limit=5,  # small batches — hang cannot strand 40 domains
                )
                if not batch:
                    break

                # Mark one-by-one as we go (not whole batch up-front)
                items = [
                    {
                        "id": row.id,
                        "domain": row.domain,
                        "company_name": row.company_name,
                    }
                    for row in batch
                ]

                async def on_domain_done(
                    queue_id: int, domain: str, company_name: str | None
                ) -> None:
                    nonlocal harvested_domains
                    await refresh_stop_flag()
                    if is_stale_run():
                        return
                    harvested_domains += 1
                    await mark_done(db, queue_id)
                    session.add_checked(domain, company_name)
                    job_controls.set_run_session(job_id, session)
                    await upsert_company_check(
                        db,
                        domain=domain,
                        company_name=company_name,
                        location=job.target_location,
                        industry=job.target_industry,
                    )
                    await db.commit()
                    job_log(job_id, "DOMAIN_DONE", domain)
                    listed = await count_queue(
                        db,
                        location=job.target_location,
                        industry=job.target_industry,
                    )
                    job.activity_message = (
                        f"Harvesting {harvested_domains}/{listed} — "
                        f"last: {domain} — leads {starting_found + saved_count}"
                    )[:500]
                    job.last_activity_at = datetime.utcnow()
                    await db.commit()

                async def on_domain_failed(queue_id: int, error: str) -> None:
                    nonlocal harvested_domains
                    await refresh_stop_flag()
                    if is_stale_run():
                        return
                    harvested_domains += 1
                    await mark_failed(db, queue_id, error)
                    await db.commit()
                    job_log(job_id, "DOMAIN_SKIP", f"{queue_id}: {error[:120]}")

                # Mark harvesting only for this small batch
                for row in batch:
                    await mark_harvesting(db, row.id)
                await db.commit()

                await harvester.harvest_domains(
                    items=items,
                    roles=roles,
                    location=job.target_location,
                    on_contact=save_contact,
                    on_activity=on_activity,
                    on_domain_done=on_domain_done,
                    on_domain_failed=on_domain_failed,
                    should_stop=should_stop_scrape,
                    # Do NOT skip via company_checks — pending queue must be harvested for emails.
                    # Only skip emails already in leads (session.emails).
                    skip_emails=session.emails,
                    skip_domains=None,
                )

                if starting_found + saved_count >= cap:
                    break

            if is_stale_run():
                return

            pending_left = await count_queue(
                db,
                location=job.target_location,
                industry=job.target_industry,
                status=QueueStatus.PENDING,
            )
            harvesting_left = await count_queue(
                db,
                location=job.target_location,
                industry=job.target_industry,
                status=QueueStatus.HARVESTING,
            )
            remaining_q = await count_queries(
                db,
                location=job.target_location,
                industry=job.target_industry,
                status=QueryStatus.PENDING,
            )
            if should_stop():
                terminal_status = JobStatus.PAUSED
                terminal_message = (
                    f"Paused — leads {starting_found + saved_count}, "
                    f"{pending_left} companies still pending"
                )[:500]
                terminal_error = "Paused by user"
                job_log(job_id, "PAUSED", terminal_message)
            elif starting_found + saved_count >= cap:
                terminal_status = JobStatus.COMPLETED
                terminal_message = (
                    f"Lead target reached — {starting_found + saved_count} leads"
                )[:500]
                terminal_error = None
                job_log(job_id, "COMPLETED", terminal_message)
            elif pending_left == 0 and harvesting_left == 0:
                if remaining_q > 0:
                    terminal_status = JobStatus.PAUSED
                    terminal_message = (
                        f"All listed companies harvested — {starting_found + saved_count} leads. "
                        f"Click Load More Companies for next batch."
                    )[:500]
                    terminal_error = None
                    job_log(job_id, "PAUSED", "queue done; queries remain")
                else:
                    terminal_status = JobStatus.COMPLETED
                    terminal_message = (
                        f"COMPLETED — {starting_found + saved_count} leads, "
                        f"all companies processed"
                    )[:500]
                    terminal_error = None
                    job_log(job_id, "COMPLETED", terminal_message)
            else:
                terminal_status = JobStatus.PAUSED
                terminal_message = (
                    f"Harvest paused — {starting_found + saved_count} leads, "
                    f"{pending_left} pending"
                )[:500]
                terminal_error = None
                job_log(job_id, "PAUSED", terminal_message)

        except Exception as e:
            logger.exception("Job %d harvest failed", job_id)
            if is_stale_run():
                return
            terminal_status = JobStatus.PAUSED
            terminal_error = str(e)[:500]
            terminal_message = f"Error: {str(e)[:200]}"
            job_log(job_id, "HARVEST_ERROR", str(e)[:200])

        finally:
            job_controls.mark_harvest_running(job_id, False)
            job_controls.clear_run_session(job_id)
            if is_stale_run():
                return
            try:
                job.total_found = starting_found + saved_count
                job.harvest_pid = None
                if terminal_status is not None:
                    job.status = terminal_status
                    job.last_error = terminal_error
                    job.activity_message = terminal_message
                    job.last_activity_at = datetime.utcnow()
                    if terminal_status == JobStatus.COMPLETED and not job.restart_count:
                        job.restart_count = 1
                    if terminal_status == JobStatus.PAUSED and not job.restart_count:
                        job.restart_count = 1
                await db.commit()
            except Exception:
                logger.exception("Job %d failed to finalize harvest state", job_id)
