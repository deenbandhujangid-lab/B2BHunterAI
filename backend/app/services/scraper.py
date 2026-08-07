from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import quote, quote_plus, urljoin, urlparse

import phonenumbers
from playwright.async_api import Page, async_playwright

from app.core.config import settings
from app.services.location_sources import get_safe_bing_queries, get_wiki_categories
from app.services.data_quality import (
    filter_scraped_emails,
    merge_page_and_fallback_emails,
    sanitize_person_name,
    company_keys,
)
from app.services.verifier import EmailVerifier

logger = logging.getLogger(__name__)


def _loop_supports_playwright() -> bool:
    """Windows SelectorEventLoop cannot spawn Playwright subprocesses."""
    if not sys.platform.startswith("win"):
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return True
    return type(loop).__name__ == "ProactorEventLoop"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
]

ROLE_EMAIL_PREFIXES: dict[str, list[str]] = {
    "Founder": ["founder", "ceo", "cofounder", "co-founder", "hello", "contact", "info"],
    "HR": ["hr", "careers", "jobs", "people", "talent", "recruitment"],
    "Admin": ["admin", "office", "info", "contact", "support", "reception"],
    "CMO": ["marketing", "cmo", "growth", "hello", "brand", "digital"],
}

SKIP_DOMAINS = {
    "wikipedia.org", "wikimedia.org", "facebook.com", "twitter.com", "x.com",
    "linkedin.com", "instagram.com", "youtube.com", "google.com",
    "bing.com", "microsoft.com", "bseindia.com", "nseindia.com",
    "crunchbase.com", "bloomberg.com", "reuters.com", "forbes.com",
    "economictimes.indiatimes.com", "indiatimes.com", "timesofindia.indiatimes.com",
    "thehindu.com", "hindustantimes.com", "indianexpress.com", "ndtv.com",
    "moneycontrol.com", "business-standard.com", "livemint.com", "financialexpress.com",
    "inc42.com", "yourstory.com", "techcrunch.com", "venturebeat.com",
    "thenationalnews.com", "bbc.com", "cnn.com", "theguardian.com",
    "thehindubusinessline.com", "cnbctv18.com", "businessworld.in",
    "glassdoor.com", "indeed.com", "ambitionbox.com", "naukrigulf.com", "naukri.com",
    "shine.com", "monster.com", "foundit.in", "timesjobs.com",
    "play.google.com", "apps.apple.com", "github.com", "medium.com",
    "zoominfo.com", "apollo.io", "rocketreach.co", "scribd.com",
    "ch-aviation.com", "indiaai.gov.in",
    # Directories / lead aggregators — not real company sites
    "justdial.com", "indiamart.com", "clutch.co", "goodfirms.co",
    "yellowpages.com", "yelp.com", "sulekha.com", "asklaila.com",
    "tradeindia.com", "exportersindia.com", "tofler.in", "zauba.com",
    "tracxn.com", "owler.com", "craft.co", "pitchbook.com",
    "duckduckgo.com", "yahoo.com", "quora.com", "reddit.com",
    "maps.google.com", "goo.gl", "bit.ly",
    # List portals — expand for company links, never enqueue as company
    "startupblink.com", "builtin.com", "registerkaro.in",
    # Dictionaries / non-company junk from list expand
    "dictionary.com", "thefreedictionary.com", "collinsdictionary.com",
    "dictionary.net", "wiktionary.org", "cambridge.org",
    "merriam-webster.com", "wordreference.com", "ldoceonline.com",
    "imdb.com", "ycombinator.com", "failory.com",
    # Catalog / identity junk that pollutes wiki+search results
    "viaf.org", "quark.cn", "wikidata.org", "archive.org",
}


def is_blocked_host(domain: str, extra: set[str] | frozenset | tuple = ()) -> bool:
    """True if domain is a skip host (exact or subdomain) — not substring (avoids x.com∋darwinbox.com)."""
    d = (domain or "").lower().replace("www.", "").strip().split("/")[0]
    if not d or "." not in d:
        return True
    for skip in (*SKIP_DOMAINS, *extra):
        s = (skip or "").lower().strip()
        if not s:
            continue
        if d == s or d.endswith("." + s):
            return True
    return False


# India / common multi-part public suffixes → keep last 3 labels as registrable domain
_MULTI_SUFFIXES = frozenset(
    {
        "co.in",
        "com.au",
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "ac.in",
        "gov.in",
        "edu.in",
        "net.in",
        "org.in",
        "firm.in",
        "gen.in",
        "ind.in",
        "com.cn",
        "edu.cn",
        "co.jp",
        "com.br",
        "co.za",
    }
)


def registrable_domain(domain: str | None) -> str | None:
    """Collapse subdomains: seller.flipkart.com → flipkart.com (dedupe)."""
    if not domain:
        return None
    d = domain.lower().replace("www.", "").strip().split("/")[0].split("?")[0]
    if not d or "." not in d or ".." in d:
        return None
    parts = [p for p in d.split(".") if p]
    if len(parts) < 2:
        return None
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])



@dataclass
class ScrapedContact:
    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    role_category: str | None = None
    company_name: str | None = None
    domain: str | None = None
    emails: list[str] = field(default_factory=list)
    phone_raw: str | None = None
    phone_e164: str | None = None
    is_phone_valid: bool = False
    source_url: str | None = None
    location: str | None = None


class LocalScraper:
    """Location-aware scraper: Wikipedia (primary, paginated) + Bing (limited fallback)."""

    def __init__(self):
        self.verifier = EmailVerifier()
        self._request_count = 0
        self._on_activity = None

    async def _delay(self, extra: float = 0.0):
        wait = max(2.0, random.uniform(settings.SCRAPE_DELAY_MIN, settings.SCRAPE_DELAY_MAX) + extra)
        await asyncio.sleep(wait)
        self._request_count += 1
        if self._request_count % settings.SAFETY_PAUSE_EVERY == 0:
            pause = random.uniform(settings.SAFETY_PAUSE_MIN, settings.SAFETY_PAUSE_MAX)
            logger.info("Anti-block pause %.0fs after %d requests...", pause, self._request_count)
            await self._notify(
                self._on_activity,
                f"Anti-block pause ({int(pause)}s) — scraping still running…",
            )
            await asyncio.sleep(pause)

    async def scrape(
        self,
        location: str,
        industry: str,
        role: str,
        target_limit: int,
        on_progress=None,
        on_contact=None,
        on_notice=None,
        on_activity=None,
        skip_emails: set[str] | None = None,
        skip_domains: set[str] | None = None,
        skip_companies: set[str] | None = None,
        on_company_checked=None,
        on_checkpoint=None,
        role_checkpoint: dict | None = None,
        job_id: int | None = None,
        should_stop=None,
    ) -> list[ScrapedContact]:
        """Public entry — on Windows SelectorEventLoop, run Playwright in a Proactor thread."""
        kwargs = dict(
            location=location,
            industry=industry,
            role=role,
            target_limit=target_limit,
            on_progress=on_progress,
            on_contact=on_contact,
            on_notice=on_notice,
            on_activity=on_activity,
            skip_emails=skip_emails,
            skip_domains=skip_domains,
            skip_companies=skip_companies,
            on_company_checked=on_company_checked,
            on_checkpoint=on_checkpoint,
            role_checkpoint=role_checkpoint,
            job_id=job_id,
            should_stop=should_stop,
        )
        if _loop_supports_playwright():
            try:
                return await self._scrape_async(**kwargs)
            except NotImplementedError:
                logger.warning(
                    "Playwright NotImplementedError on current loop — falling back to Proactor thread"
                )

        return await self._scrape_via_proactor_thread(**kwargs)

    async def _scrape_via_proactor_thread(self, **kwargs) -> list[ScrapedContact]:
        """Run async Playwright inside a fresh Windows ProactorEventLoop in a worker thread."""
        main_loop = asyncio.get_running_loop()
        on_progress = kwargs.get("on_progress")
        on_contact = kwargs.get("on_contact")
        on_notice = kwargs.get("on_notice")
        on_activity = kwargs.get("on_activity")
        should_stop = kwargs.get("should_stop")

        async def bridge_progress(count: int):
            if not on_progress:
                return
            fut = asyncio.run_coroutine_threadsafe(on_progress(count), main_loop)
            await asyncio.wrap_future(fut)

        async def bridge_contact(contact: ScrapedContact):
            if not on_contact:
                return
            fut = asyncio.run_coroutine_threadsafe(on_contact(contact), main_loop)
            await asyncio.wrap_future(fut)

        async def bridge_notice(msg: str):
            if not on_notice:
                return
            fut = asyncio.run_coroutine_threadsafe(on_notice(msg), main_loop)
            await asyncio.wrap_future(fut)

        async def bridge_activity(msg: str):
            if not on_activity:
                return
            fut = asyncio.run_coroutine_threadsafe(on_activity(msg), main_loop)
            await asyncio.wrap_future(fut)

        def bridge_stop() -> bool:
            if not should_stop:
                return False
            return bool(should_stop())

        thread_kwargs = {
            **kwargs,
            "on_progress": bridge_progress if on_progress else None,
            "on_contact": bridge_contact if on_contact else None,
            "on_notice": bridge_notice if on_notice else None,
            "on_activity": bridge_activity if on_activity else None,
            "should_stop": bridge_stop if should_stop else None,
        }

        def runner():
            if sys.platform.startswith("win"):
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(self._scrape_async(**thread_kwargs))
            finally:
                loop.close()

        logger.info("Scraping via Windows Proactor thread (uvicorn loop incompatible with Playwright)")
        return await asyncio.to_thread(runner)

    async def _scrape_async(
        self,
        location: str,
        industry: str,
        role: str,
        target_limit: int,
        on_progress=None,
        on_contact=None,
        on_notice=None,
        on_activity=None,
        skip_emails: set[str] | None = None,
        skip_domains: set[str] | None = None,
        skip_companies: set[str] | None = None,
        on_company_checked=None,
        on_checkpoint=None,
        role_checkpoint: dict | None = None,
        job_id: int | None = None,
        should_stop=None,
    ) -> list[ScrapedContact]:
        self._on_activity = on_activity
        contacts: list[ScrapedContact] = []
        # Shared with orchestrator session — same set objects, updated on each save
        known_emails = skip_emails if skip_emails is not None else set()
        known_domains = skip_domains if skip_domains is not None else set()
        known_companies = skip_companies if skip_companies is not None else set()
        skipped_known = 0

        try:
            from playwright_stealth import Stealth
            pw_ctx = Stealth().use_async(async_playwright())
        except ImportError:
            pw_ctx = async_playwright()

        async with pw_ctx as pw:
            browser = await pw.chromium.launch(
                headless=settings.SCRAPER_HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                context = await browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": 1920, "height": 1080},
                    locale="en-IN",
                )
                page = await context.new_page()

                logger.info(
                    "Starting scrape: %s / %s / %s (target=%d, DB skip: %d cos / %d dom / %d em)",
                    location, industry, role, target_limit,
                    len(known_companies), len(known_domains), len(known_emails),
                )

                wiki_contacts, skipped_known = await self._scrape_wikipedia(
                    page, location, industry, role, target_limit,
                    on_progress, known_emails, known_domains, known_companies,
                    should_stop=should_stop,
                    on_contact=on_contact, on_activity=on_activity,
                    on_company_checked=on_company_checked,
                    on_checkpoint=on_checkpoint,
                    role_checkpoint=role_checkpoint or {},
                )
                contacts.extend(wiki_contacts)
                logger.info(
                    "Wikipedia: %d new contacts (%d already-known skipped)",
                    len(wiki_contacts), skipped_known,
                )

                # Phase B: always discover SMEs via search (not only when wiki underfills)
                run_search = (
                    not settings.SKIP_SEARCH_ENGINES
                    and not (should_stop and should_stop())
                    and (
                        settings.ALWAYS_RUN_SEARCH_DISCOVERY
                        or len(contacts) < target_limit
                    )
                )
                if run_search and len(contacts) < target_limit:
                    remaining = target_limit - len(contacts)
                    await self._notify(
                        on_activity,
                        f"Search discovery (SMEs) — {remaining} slots left…",
                    )
                    search_contacts = await self._scrape_search_engines_with_recovery(
                        page, location, industry, role,
                        remaining, known_domains, on_progress, len(contacts),
                        known_emails, should_stop=should_stop,
                        on_contact=on_contact,
                        on_notice=on_notice,
                        on_activity=on_activity,
                        on_company_checked=on_company_checked,
                        known_companies=known_companies,
                    )
                    contacts.extend(search_contacts)
                    logger.info("Search discovery added %d new contacts", len(search_contacts))
            finally:
                try:
                    await browser.close()
                except Exception:
                    logger.debug("Browser close skipped", exc_info=True)

        contacts = [c for c in contacts if c.emails]
        logger.info("Scrape done: %d contacts with emails for %s", len(contacts), location)
        return contacts[:target_limit]

    def _remember_company(self, name: str, known_companies: set[str]) -> None:
        known_companies.update(company_keys(name))

    def _company_is_known(self, name: str, known_companies: set[str]) -> bool:
        return bool(company_keys(name) & known_companies)

    async def _fetch_official_website(self, page: Page, title: str) -> str | None:
        """Resolve official site via Wikidata P856, then filtered Wikipedia links."""
        # 1) Wikidata official website (P856) — most reliable
        try:
            api = (
                "https://en.wikipedia.org/w/api.php"
                f"?action=query&titles={quote_plus(title)}"
                "&prop=pageprops&ppprop=wikibase_item&format=json"
            )
            await page.goto(api, wait_until="domcontentloaded", timeout=12000)
            raw = await page.evaluate("() => document.body.innerText")
            data = json.loads(raw)
            pages = (data.get("query") or {}).get("pages") or {}
            qid = None
            for p in pages.values():
                qid = (p.get("pageprops") or {}).get("wikibase_item")
                if qid:
                    break
            if qid:
                wd = (
                    "https://www.wikidata.org/w/api.php"
                    f"?action=wbgetclaims&entity={quote_plus(qid)}"
                    "&property=P856&format=json"
                )
                await page.goto(wd, wait_until="domcontentloaded", timeout=12000)
                raw = await page.evaluate("() => document.body.innerText")
                wd_data = json.loads(raw)
                claims = ((wd_data.get("claims") or {}).get("P856")) or []
                for claim in claims:
                    mainsnak = claim.get("mainsnak") or {}
                    dv = (mainsnak.get("datavalue") or {}).get("value")
                    if isinstance(dv, str) and dv.startswith("http"):
                        host = urlparse(dv).netloc.replace("www.", "").lower()
                        if host and self._is_valid_domain(host):
                            return dv
        except Exception as e:
            logger.debug("Wikidata P856 failed for %s: %s", title, e)

        # 2) Wikipedia externallinks — scored, news sites rejected
        return await self._fetch_wiki_website_via_api(page, title)

    async def _fetch_wiki_website_via_api(self, page: Page, title: str) -> str | None:
        """Wikipedia externallinks with news/media filtering + company-name scoring."""
        api = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=parse&page={quote_plus(title)}&prop=externallinks|text&format=json"
        )
        try:
            await page.goto(api, wait_until="domcontentloaded", timeout=15000)
            raw = await page.evaluate("() => document.body.innerText")
            data = json.loads(raw)
            parse = data.get("parse") or {}
            links = parse.get("externallinks") or []

            # Prefer "Official website" if present in HTML
            html = (parse.get("text") or {}).get("*") or ""
            m = re.search(
                r'[Oo]fficial\s+website[^<]{0,80}<a[^>]+href="(https?://[^"]+)"',
                html,
            )
            if m:
                cand = m.group(1)
                host = urlparse(cand).netloc.replace("www.", "").lower()
                if host and self._is_valid_domain(host):
                    return cand

            name_tokens = {
                t for t in re.findall(r"[a-z0-9]+", title.lower())
                if len(t) > 2 and t not in {
                    "the", "and", "ltd", "limited", "pvt", "private", "inc",
                    "corp", "company", "group", "india", "indian",
                }
            }

            scored: list[tuple[int, str]] = []
            for link in links:
                if not link.startswith("http"):
                    continue
                low = link.lower()
                host = urlparse(link).netloc.replace("www.", "").lower()
                if not host or not self._is_valid_domain(host):
                    continue
                # Skip news / social / directory noise
                if any(
                    x in low
                    for x in (
                        "wikipedia", "wikimedia", "facebook", "twitter", "x.com/",
                        "linkedin", "instagram", "youtube", "archive.org",
                        "bseindia", "nseindia", "web.archive",
                    )
                ):
                    continue
                score = 0
                # Domain shares company name tokens → likely official
                host_core = host.split(".")[0]
                for tok in name_tokens:
                    if tok in host_core or tok in host:
                        score += 5
                if host.endswith(".in") or host.endswith(".com"):
                    score += 1
                if "/wiki/" in low or "news" in host or "times" in host:
                    score -= 10
                scored.append((score, link))

            if not scored:
                return None
            scored.sort(key=lambda x: (-x[0], len(x[1])))
            best_score, best = scored[0]
            # Require some name match when possible; otherwise take best non-news
            if best_score <= 0 and name_tokens:
                # No name match — still allow if only one decent .com/.in left
                decent = [u for s, u in scored if s >= 0]
                return decent[0] if decent else None
            return best
        except Exception as e:
            logger.debug("Wiki API website lookup failed for %s: %s", title, e)
        return None

    async def _record_checked(self, on_company_checked, name: str, domain: str | None) -> None:
        if not on_company_checked or not domain:
            return
        if not self._is_valid_domain(domain):
            return
        try:
            result = on_company_checked(name, domain)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.debug("on_company_checked failed: %s", e)

    async def _save_progress(
        self, on_checkpoint, category: str, index: int, name: str
    ) -> None:
        if not on_checkpoint:
            return
        try:
            result = on_checkpoint(category, index, name)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.debug("on_checkpoint failed: %s", e)

    async def _scrape_wikipedia(
        self,
        page: Page,
        location: str,
        industry: str,
        role: str,
        limit: int,
        on_progress=None,
        known_emails: set[str] | None = None,
        known_domains: set[str] | None = None,
        known_companies: set[str] | None = None,
        should_stop=None,
        on_contact=None,
        on_activity=None,
        on_company_checked=None,
        on_checkpoint=None,
        role_checkpoint: dict | None = None,
    ) -> tuple[list[ScrapedContact], int]:
        contacts: list[ScrapedContact] = []
        skipped = 0
        categories = get_wiki_categories(location, industry)
        role_checkpoint = role_checkpoint or {}
        resume_cat = role_checkpoint.get("category")
        resume_idx = int(role_checkpoint.get("company_index") or 0)
        passed_resume = not resume_cat

        for category in categories:
            if should_stop and should_stop():
                logger.info("Pause requested — stopping Wikipedia scrape")
                break
            if len(contacts) >= limit:
                break

            if resume_cat and not passed_resume:
                if category != resume_cat:
                    continue
                passed_resume = True

            cat_start = resume_idx if category == resume_cat else 0

            cat_contacts, cat_skipped = await self._scrape_wiki_category_paginated(
                page, category, location, role, limit - len(contacts), on_progress,
                offset=len(contacts), known_emails=known_emails,
                known_domains=known_domains, known_companies=known_companies,
                should_stop=should_stop, on_contact=on_contact, on_activity=on_activity,
                on_company_checked=on_company_checked, on_checkpoint=on_checkpoint,
                start_index=cat_start,
            )
            resume_idx = 0
            contacts.extend(cat_contacts)
            skipped += cat_skipped
            await self._delay(extra=settings.CATEGORY_PAUSE)

        return contacts, skipped

    async def _scrape_wiki_category_paginated(
        self,
        page: Page,
        category: str,
        location: str,
        role: str,
        limit: int,
        on_progress=None,
        offset: int = 0,
        known_emails: set[str] | None = None,
        known_domains: set[str] | None = None,
        known_companies: set[str] | None = None,
        should_stop=None,
        on_contact=None,
        on_activity=None,
        on_company_checked=None,
        on_checkpoint=None,
        start_index: int = 0,
    ) -> tuple[list[ScrapedContact], int]:
        contacts: list[ScrapedContact] = []
        skipped = 0
        known_emails = known_emails if known_emails is not None else set()
        known_domains = known_domains if known_domains is not None else set()
        known_companies = known_companies if known_companies is not None else set()

        companies = await self._fetch_wiki_category_members(page, category)
        logger.info(
            "Wiki %s via API: %d companies (resume index %d)",
            category, len(companies), start_index,
        )
        if start_index > 0 and on_activity:
            await self._notify(
                on_activity,
                f"Checkpoint: skipping first {start_index} companies (already done)…",
            )

        checkpoint_skipped = 0
        for i, company in enumerate(companies):
            if should_stop and should_stop():
                break
            if len(contacts) >= limit:
                break
            if len(contacts) >= settings.SCRAPER_MAX_COMPANIES_PER_CAT:
                break

            cname = company["name"]

            # Already processed in a prior run of this job — zero-cost skip
            if i < start_index:
                skipped += 1
                checkpoint_skipped += 1
                continue

            if self._company_is_known(cname, known_companies):
                skipped += 1
                if skipped <= 3 or skipped % 25 == 0:
                    await self._notify(on_activity, f"Skip (in DB): {cname}")
                await self._save_progress(on_checkpoint, category, i + 1, cname)
                continue

            # Domain already known? Resolve official site cheaply — no full scrape.
            quick_site = await self._fetch_official_website(page, cname)
            quick_domain = None
            if quick_site:
                quick_domain = urlparse(quick_site).netloc.replace("www.", "").lower()
                if quick_domain in known_domains:
                    self._remember_company(cname, known_companies)
                    skipped += 1
                    if skipped <= 3 or skipped % 25 == 0:
                        await self._notify(
                            on_activity,
                            f"Skip (checked): {cname} ({quick_domain})",
                        )
                    await self._save_progress(on_checkpoint, category, i + 1, cname)
                    continue

            await self._notify(on_activity, f"Checking {cname}…")
            contact = await self._scrape_wiki_company_page(
                page, cname, company["href"], role, location
            )

            domain = None
            if contact and contact.domain:
                domain = contact.domain.lower().replace("www.", "")
            elif quick_domain:
                domain = quick_domain

            if not contact or not domain:
                if domain:
                    await self._record_checked(on_company_checked, cname, domain)
                await self._save_progress(on_checkpoint, category, i + 1, cname)
                await self._delay(extra=-4.0 if domain else 0)
                continue

            if domain in known_domains or not self._is_valid_domain(domain):
                self._remember_company(cname, known_companies)
                await self._record_checked(on_company_checked, cname, domain)
                skipped += 1
                await self._save_progress(on_checkpoint, category, i + 1, cname)
                await self._delay(extra=-4.0)
                continue

            if contact:
                contact.emails = [
                    e for e in contact.emails if e.lower() not in known_emails
                ]
            if not contact or not contact.emails:
                await self._record_checked(on_company_checked, cname, domain)
                skipped += 1
                await self._save_progress(on_checkpoint, category, i + 1, cname)
                await self._delay(extra=-4.0)
                continue

            known_domains.add(domain)
            self._remember_company(cname, known_companies)
            for e in contact.emails:
                known_emails.add(e.lower())
            contacts.append(contact)
            if on_contact:
                await on_contact(contact)
            if on_progress:
                await on_progress(offset + len(contacts))
            await self._record_checked(on_company_checked, cname, domain)
            await self._save_progress(on_checkpoint, category, i + 1, cname)
            await self._delay()

        if checkpoint_skipped and on_activity:
            await self._notify(
                on_activity,
                f"Checkpoint skipped {checkpoint_skipped} companies — continuing…",
            )

        return contacts, skipped

    async def _fetch_wiki_category_members(self, page: Page, category: str) -> list[dict]:
        """List category members via Wikipedia API (paginated)."""
        members: list[dict] = []
        cmcontinue = None
        title = category if category.startswith("Category:") else f"Category:{category}"

        for _ in range(settings.SCRAPER_MAX_WIKI_PAGES):
            api = (
                "https://en.wikipedia.org/w/api.php"
                f"?action=query&list=categorymembers&cmtitle={quote_plus(title)}"
                f"&cmtype=page&cmlimit=100&format=json"
            )
            if cmcontinue:
                api += f"&cmcontinue={quote_plus(cmcontinue)}"
            try:
                await page.goto(api, wait_until="domcontentloaded", timeout=settings.SCRAPER_TIMEOUT_MS)
                raw = await page.evaluate("() => document.body.innerText")
                data = json.loads(raw)
            except Exception as e:
                logger.warning("Wiki API failed for %s: %s", title, e)
                break

            for m in data.get("query", {}).get("categorymembers", []):
                name = (m.get("title") or "").strip()
                pageid = m.get("pageid")
                if not name or name.startswith("List of") or name.startswith("Talk:"):
                    continue
                if pageid:
                    href = f"https://en.wikipedia.org/?curid={pageid}"
                else:
                    href = "https://en.wikipedia.org/wiki/" + quote(name.replace(" ", "_"), safe="()_")
                members.append({"name": name, "href": href})

            cont = data.get("continue") or {}
            cmcontinue = cont.get("cmcontinue")
            if not cmcontinue:
                break
            await self._delay(extra=-6.0)

        return members

    async def _harvest_emails_from_page(self, page: Page) -> set[str]:
        """Pull emails from mailto, footer, contact blocks, body, Cloudflare obfuscation."""
        found: set[str] = set()
        try:
            raw = await page.evaluate(
                """() => {
                const out = { mailto: [], text: '', htmlBits: [], cf: [] };
                for (const a of document.querySelectorAll('a[href^="mailto:"]')) {
                    const h = (a.getAttribute('href') || '').replace(/^mailto:/i, '').split('?')[0];
                    if (h) out.mailto.push(h);
                }
                for (const el of document.querySelectorAll('[data-cfemail]')) {
                    out.cf.push(el.getAttribute('data-cfemail') || '');
                }
                const parts = [];
                for (const sel of ['footer', '.footer', '#footer', '.site-footer',
                                   '[class*="contact"]', '[id*="contact"]',
                                   '[class*="Contact"]', 'address']) {
                    for (const el of document.querySelectorAll(sel)) {
                        parts.push(el.innerText || '');
                        parts.push(el.innerHTML || '');
                    }
                }
                out.htmlBits = parts;
                out.text = document.body ? (document.body.innerText || '') : '';
                out.html = document.documentElement ? document.documentElement.innerHTML.slice(0, 500000) : '';
                return out;
            }"""
            )
        except Exception as e:
            logger.debug("harvest evaluate failed: %s", e)
            try:
                content = await page.content()
                found.update(self.verifier.extract_emails(content))
            except Exception:
                pass
            return found

        for m in raw.get("mailto") or []:
            found.update(self.verifier.extract_emails(m))
        for bit in raw.get("htmlBits") or []:
            found.update(self.verifier.extract_emails(bit))
        found.update(self.verifier.extract_emails(raw.get("text") or ""))
        found.update(self.verifier.extract_emails(raw.get("html") or ""))

        # Cloudflare email decode: data-cfemail
        for enc in raw.get("cf") or []:
            decoded = self._decode_cfemail(enc)
            if decoded:
                found.add(decoded.lower())

        return {e for e in found if e and "@" in e}

    @staticmethod
    def _decode_cfemail(enc: str) -> str | None:
        try:
            data = bytes.fromhex(enc)
            key = data[0]
            return "".join(chr(b ^ key) for b in data[1:])
        except Exception:
            return None

    async def _discover_contact_urls(self, page: Page, base_url: str) -> list[str]:
        """Find Contact / About / Team links from the current page."""
        try:
            hrefs = await page.evaluate(
                """() => {
                const keys = ['contact', 'about', 'team', 'leadership', 'reach-us',
                              'get-in-touch', 'connect', 'support', 'enquire', 'inquiry'];
                const out = [];
                for (const a of document.querySelectorAll('a[href]')) {
                    const href = a.href || '';
                    const text = (a.innerText || a.textContent || '').toLowerCase();
                    const h = href.toLowerCase();
                    if (!href.startsWith('http')) continue;
                    if (keys.some(k => h.includes(k) || text.includes(k))) out.push(href);
                }
                return [...new Set(out)].slice(0, 8);
            }"""
            )
        except Exception:
            hrefs = []

        paths = [
            "/contact", "/contact-us", "/contactus", "/about", "/about-us", "/aboutus",
            "/team", "/our-team", "/leadership", "/company", "/reach-us", "/get-in-touch",
            "/support", "/enquire", "/enquiry", "/connect",
        ]
        candidates: list[str] = []
        for p in paths:
            candidates.append(urljoin(base_url, p))
        for h in hrefs or []:
            if urlparse(h).netloc.replace("www.", "").lower() == urlparse(base_url).netloc.replace("www.", "").lower():
                candidates.append(h)

        # de-dupe preserve order
        seen: set[str] = set()
        out: list[str] = []
        for u in candidates:
            key = u.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(u)
        return out[:2]

    async def _scrape_site_for_emails(
        self, page: Page, website: str, location: str
    ) -> tuple[set[str], tuple[str | None, str | None, bool]]:
        """Homepage + contact/about/footer harvest."""
        page_emails: set[str] = set()
        phone = (None, None, False)
        try:
            await page.goto(website, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(random.randint(400, 900))
            # Scroll so lazy footers load
            try:
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(300)
            except Exception:
                pass

            page_emails.update(await self._harvest_emails_from_page(page))
            body = await page.inner_text("body") if await page.query_selector("body") else ""
            phone = self._parse_phone(body, location)

            for url in await self._discover_contact_urls(page, website):
                try:
                    r = await page.goto(url, wait_until="domcontentloaded", timeout=8000)
                    if not r or r.status >= 400:
                        continue
                    await page.wait_for_timeout(300)
                    try:
                        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(200)
                    except Exception:
                        pass
                    page_emails.update(await self._harvest_emails_from_page(page))
                except Exception:
                    continue
        except Exception as e:
            logger.debug("Site harvest %s failed: %s", website, e)

        return page_emails, phone

    async def _scrape_wiki_company_page(
        self, page: Page, name: str, wiki_url: str, role: str, location: str
    ) -> ScrapedContact | None:
        try:
            await page.goto(wiki_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(random.randint(600, 1200))
            names = await self._extract_founder_names_from_wiki(page, role)

            # Wikidata official site first — NEVER pick news article links
            website = await self._fetch_official_website(page, name)
            if not website:
                website = await page.evaluate("""() => {
                    const row = [...document.querySelectorAll('.infobox tr')].find(tr => {
                        const th = tr.querySelector('th');
                        return th && /website|url/i.test(th.innerText || '');
                    });
                    if (row) {
                        const a = row.querySelector('a[href^="http"]');
                        if (a) return a.href;
                    }
                    return null;
                }""")

            domain = None
            page_emails: set[str] = set()
            phone_raw, phone_e164, is_valid = None, None, False

            if website:
                domain = urlparse(website).netloc.replace("www.", "")
                if self._is_valid_domain(domain):
                    page_emails, (phone_raw, phone_e164, is_valid) = await self._scrape_site_for_emails(
                        page, website, location
                    )
                    if not page_emails and website.startswith("http://"):
                        https_url = "https://" + website[len("http://"):]
                        page_emails, phone2 = await self._scrape_site_for_emails(
                            page, https_url, location
                        )
                        if page_emails:
                            website = https_url
                            phone_raw, phone_e164, is_valid = phone2

            if not domain or not self._is_valid_domain(domain):
                return None

            emails = merge_page_and_fallback_emails(page_emails, domain)
            if not emails:
                logger.info("No emails found on site for %s (%s)", name, domain)
                return None

            logger.info(
                "Found %d emails for %s (%s): %s", len(emails), name, domain, emails[:3]
            )
            first, last = sanitize_person_name(names.get("first"), names.get("last"))

            return ScrapedContact(
                first_name=first,
                last_name=last,
                company_name=name,
                domain=domain,
                job_title=role,
                role_category=role,
                emails=emails,
                phone_raw=phone_raw,
                phone_e164=phone_e164,
                is_phone_valid=is_valid,
                source_url=website or wiki_url,
                location=location,
            )
        except Exception as e:
            logger.debug("Wiki company %s failed: %s", name, e)
            return None

    async def _extract_founder_names_from_wiki(self, page: Page, role: str) -> dict:
        """Extract founder/CEO names from Wikipedia infobox."""
        try:
            text = await page.evaluate("""() => {
                const infobox = document.querySelector('.infobox, .vcard');
                return infobox ? infobox.innerText : document.body.innerText.slice(0, 3000);
            }""")
            return self._extract_names(text, role)
        except Exception:
            return {}

    async def _scrape_search_engines_with_recovery(
        self,
        page: Page,
        location: str,
        industry: str,
        role: str,
        limit: int,
        seen: set[str],
        on_progress=None,
        offset: int = 0,
        known_emails: set[str] | None = None,
        should_stop=None,
        on_contact=None,
        on_notice=None,
        on_activity=None,
        on_company_checked=None,
        known_companies: set[str] | None = None,
    ) -> list[ScrapedContact]:
        """
        Phase B SME discovery: Bing → CAPTCHA → DuckDuckGo.
        Company name = result title; domain = result host (no Wikipedia required).
        """
        contacts: list[ScrapedContact] = []
        known_emails = known_emails if known_emails is not None else set()
        known_companies = known_companies if known_companies is not None else set()
        queries = get_safe_bing_queries(location, industry, role)[
            : settings.SCRAPER_MAX_BING_QUERIES
        ]

        engines = [
            ("bing", self._fetch_bing_results),
            ("duckduckgo", self._fetch_ddg_results),
        ]
        engine_idx = 0
        captcha_hits = 0

        for query in queries:
            if should_stop and should_stop():
                break
            if len(contacts) >= limit:
                break
            if engine_idx >= len(engines):
                logger.warning("All search engines blocked — continuing without more discovery")
                await self._notify(
                    on_notice,
                    "Search engines blocked (CAPTCHA). Wikipedia results remain — job not stopped.",
                )
                break

            engine_name, fetch_fn = engines[engine_idx]
            try:
                await page.context.clear_cookies()
                await page.set_extra_http_headers({
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept-Language": "en-IN,en;q=0.9",
                })
                await self._notify(on_activity, f"Search: {query[:60]}…")

                items, blocked = await fetch_fn(page, query)
                if blocked:
                    captcha_hits += 1
                    msg = (
                        f"{engine_name.title()} CAPTCHA/block detected (#{captcha_hits}) — "
                        f"cooling down {int(settings.BING_CAPTCHA_COOLDOWN)}s, then switching engine…"
                    )
                    logger.warning(msg)
                    await self._notify(on_notice, msg)
                    await asyncio.sleep(settings.BING_CAPTCHA_COOLDOWN)
                    engine_idx += 1
                    if engine_idx < len(engines):
                        next_name, next_fn = engines[engine_idx]
                        items, blocked = await next_fn(page, query)
                        if blocked:
                            logger.warning("%s also blocked — stopping search discovery", next_name)
                            await self._notify(
                                on_notice,
                                f"{next_name.title()} bhi blocked — search discovery pause.",
                            )
                            break
                    else:
                        break

                if not items:
                    await asyncio.sleep(settings.BING_EXTRA_DELAY)
                    continue

                for item in items:
                    if should_stop and should_stop():
                        break
                    if len(contacts) >= limit:
                        break
                    target_url = item.get("url") or self._resolve_bing_url(item)
                    if not target_url:
                        continue
                    domain = urlparse(target_url).netloc.replace("www.", "").lower()
                    if domain in seen or not self._is_valid_domain(domain):
                        continue

                    title = item.get("title") or domain
                    if self._company_is_known(title, known_companies):
                        continue

                    await self._notify(on_activity, f"SME check: {title[:50]}…")
                    # Prefer company homepage root over deep article URLs
                    home = f"{urlparse(target_url).scheme}://{urlparse(target_url).netloc}/"
                    contact = await self._scrape_company_site(
                        page, home, title, domain, role, location
                    )
                    if not contact:
                        await self._record_checked(on_company_checked, title, domain)
                        seen.add(domain)
                        continue

                    contact.emails = [
                        e for e in contact.emails if e.lower() not in known_emails
                    ]
                    if not contact.emails:
                        await self._record_checked(on_company_checked, title, domain)
                        seen.add(domain)
                        continue

                    seen.add(domain)
                    self._remember_company(title, known_companies)
                    for e in contact.emails:
                        known_emails.add(e.lower())
                    contacts.append(contact)
                    if on_contact:
                        await on_contact(contact)
                    if on_progress:
                        await on_progress(offset + len(contacts))
                    await self._record_checked(on_company_checked, title, domain)
                    await self._delay()

                await asyncio.sleep(settings.BING_EXTRA_DELAY)

            except Exception as e:
                logger.warning("Search engine query failed: %s", e)

        return contacts

    async def _notify(self, on_notice, message: str) -> None:
        if not on_notice:
            return
        try:
            result = on_notice(message)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.debug("on_notice failed: %s", e)

    def _is_blocked_page(self, body: str) -> bool:
        b = body.lower()
        markers = [
            "captcha", "challenge", "unusual traffic", "verify you are a human",
            "access denied", "blocked", "one last step", "solve the challenge",
            "are you a robot", "security check",
        ]
        return any(m in b for m in markers)

    async def _fetch_bing_results(self, page: Page, query: str) -> tuple[list[dict], bool]:
        url = f"https://www.bing.com/search?q={quote_plus(query)}&count=20"
        await page.goto(url, wait_until="domcontentloaded", timeout=settings.SCRAPER_TIMEOUT_MS)
        await page.wait_for_timeout(random.randint(2500, 4000))
        body = await page.inner_text("body") if await page.query_selector("body") else ""
        if self._is_blocked_page(body):
            return [], True
        items = await page.eval_on_selector_all(
            "li.b_algo",
            """els => els.map(li => ({
                title: li.querySelector('h2')?.innerText || '',
                href: li.querySelector('h2 a')?.href || '',
                cite: li.querySelector('cite')?.innerText || '',
                url: ''
            })).slice(0, 30)""",
        )
        for it in items:
            it["url"] = self._resolve_bing_url(it) or ""
        return items, False

    async def _fetch_google_results(self, page: Page, query: str) -> tuple[list[dict], bool]:
        """Google web search — slower cadence; CAPTCHA → caller switches to Bing/DDG."""
        url = f"https://www.google.com/search?q={quote_plus(query)}&num=20&hl=en"
        await page.goto(url, wait_until="domcontentloaded", timeout=settings.SCRAPER_TIMEOUT_MS)
        await page.wait_for_timeout(random.randint(3000, 5500))
        body = await page.inner_text("body") if await page.query_selector("body") else ""
        blocked_markers = [
            "unusual traffic", "captcha", "sorry, but your computer",
            "enable javascript", "before you continue", "g-recaptcha",
        ]
        body_l = (body or "").lower()
        if self._is_blocked_page(body) or any(m in body_l for m in blocked_markers):
            return [], True
        items = await page.eval_on_selector_all(
            "div.g, div[data-sokoban-container]",
            """els => els.map(el => {
                const a = el.querySelector('a[href^="http"]');
                const h3 = el.querySelector('h3');
                const cite = el.querySelector('cite');
                return {
                    title: h3 ? h3.innerText : '',
                    href: a ? a.href : '',
                    cite: cite ? cite.innerText : '',
                    url: a ? a.href : ''
                };
            }).filter(x => x.url && !x.url.includes('google.com')).slice(0, 30)""",
        )
        return items or [], False

    async def _fetch_ddg_results(self, page: Page, query: str) -> tuple[list[dict], bool]:
        """DuckDuckGo HTML version — often less CAPTCHA than Bing."""
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        await page.goto(url, wait_until="domcontentloaded", timeout=settings.SCRAPER_TIMEOUT_MS)
        await page.wait_for_timeout(random.randint(2500, 4000))
        body = await page.inner_text("body") if await page.query_selector("body") else ""
        if self._is_blocked_page(body):
            return [], True
        items = await page.eval_on_selector_all(
            "a.result__a",
            """els => els.map(e => ({
                title: e.innerText || '',
                href: e.href || '',
                cite: '',
                url: e.href || ''
            })).slice(0, 30)""",
        )
        # Filter duckduckgo redirect wrappers
        cleaned = []
        for it in items:
            u = it.get("url", "")
            if "duckduckgo.com" in u:
                continue
            if u.startswith("http"):
                cleaned.append(it)
        return cleaned, False

    async def _fetch_google_serpapi(self, query: str) -> tuple[list[dict], bool]:
        """Google via SerpAPI — multi-page. Uses DB JSON cache (0 credits on hit)."""
        import httpx

        from app.services.serpapi_cache import (
            get_cached_response,
            items_from_serpapi_json,
            save_cached_response,
        )

        key = (settings.SERPAPI_KEY or "").strip()
        # Each page ≈ 1 credit. Keep low — deep pages are mostly duplicates/listicles.
        pages = max(1, min(int(settings.SERPAPI_PAGES or 1), 5))
        min_continue = max(1, int(getattr(settings, "SERPAPI_MIN_RESULTS_CONTINUE", 4) or 4))
        seen_urls: set[str] = set()
        items: list[dict] = []
        self._last_serpapi_from_cache = False
        cache_hits = 0
        live_fetches = 0

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0)) as client:
                for page_i in range(pages):
                    start = page_i * 10
                    data = await get_cached_response(
                        query, engine="google", start_offset=start
                    )
                    from_cache = data is not None

                    if not from_cache:
                        if not key:
                            # No key and no cache — soft fail so caller can fallback
                            if not items:
                                return [], True
                            break
                        r = await client.get(
                            "https://serpapi.com/search.json",
                            params={
                                "engine": "google",
                                "q": query,
                                "num": 10,
                                "start": start,
                                "hl": "en",
                                "gl": "in",
                                "api_key": key,
                            },
                        )
                        if r.status_code in (401, 403):
                            logger.info(
                                "SerpAPI key unavailable (HTTP %s) — using fallback search",
                                r.status_code,
                            )
                            return ([], True) if not items else (items, False)
                        if r.status_code == 429:
                            logger.info("SerpAPI rate limit — using fallback if needed")
                            return (items, False) if items else ([], True)
                        if r.status_code >= 400:
                            logger.info("SerpAPI HTTP %s — soft skip", r.status_code)
                            return (items, False) if items else ([], True)
                        data = r.json()
                        err = data.get("error")
                        if err:
                            msg = str(err).lower()
                            if "api key" in msg or "invalid" in msg:
                                logger.info("SerpAPI key invalid — using fallback search")
                            else:
                                logger.info("SerpAPI: %s", str(err)[:120])
                            return (items, False) if items else ([], True)
                        await save_cached_response(
                            query, data, engine="google", start_offset=start
                        )
                        live_fetches += 1
                    else:
                        cache_hits += 1

                    organic = data.get("organic_results") or []
                    if not organic:
                        break
                    before = len(items)
                    for it in items_from_serpapi_json(data):
                        link = it.get("url") or ""
                        if not link or link in seen_urls:
                            continue
                        seen_urls.add(link)
                        items.append(it)
                    added = len(items) - before
                    # Empty/thin page or almost no new URLs → stop burning credits
                    if len(organic) < min_continue or added == 0:
                        break
                    if page_i + 1 < pages and not from_cache:
                        await asyncio.sleep(0.25)

            self._last_serpapi_from_cache = cache_hits > 0 and live_fetches == 0
            if cache_hits and live_fetches:
                logger.info(
                    "SerpAPI mixed: %s cache hits, %s live (credits)",
                    cache_hits,
                    live_fetches,
                )
            return items, False
        except Exception as e:
            logger.info("SerpAPI unavailable (%s) — using fallback search", e)
            return (items, False) if items else ([], True)

    async def _fetch_google_cse(self, query: str) -> tuple[list[dict], bool]:
        """Google Programmable Search (Custom Search JSON API)."""
        import httpx

        key = (settings.GOOGLE_CSE_API_KEY or "").strip()
        cx = (settings.GOOGLE_CSE_CX or "").strip()
        if not key or not cx:
            return [], True
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=12.0)) as client:
                r = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={"key": key, "cx": cx, "q": query, "num": 10},
                )
                if r.status_code == 429 or r.status_code == 403:
                    logger.warning("Google CSE blocked/quota: %s", r.status_code)
                    return [], True
                if r.status_code >= 400:
                    logger.warning("Google CSE HTTP %s", r.status_code)
                    return [], True
                data = r.json()
                items: list[dict] = []
                for row in data.get("items") or []:
                    link = (row.get("link") or "").strip()
                    if not link.startswith("http"):
                        continue
                    items.append(
                        {
                            "title": (row.get("title") or "")[:255],
                            "href": link,
                            "cite": "",
                            "url": link,
                        }
                    )
                return items, False
        except Exception as e:
            logger.warning("Google CSE failed: %s", e)
            return [], True

    async def _fetch_ddg_http(self, query: str) -> tuple[list[dict], bool]:
        """DuckDuckGo HTML via httpx — primary listing path (avoids Google CAPTCHA)."""
        import httpx

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-IN,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://html.duckduckgo.com/",
        }
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(20.0, connect=10.0),
                verify=False,
                headers=headers,
            ) as client:
                r = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query, "b": ""},
                )
                html = r.text or ""
                if r.status_code >= 400 or self._is_blocked_page(html):
                    return [], True
                # result links: class result__a href="..."
                items: list[dict] = []
                for m in re.finditer(
                    r'class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]*)',
                    html,
                    re.I,
                ):
                    href, title = m.group(1), (m.group(2) or "").strip()
                    # unwrap uddg redirect
                    if "uddg=" in href:
                        from urllib.parse import parse_qs, unquote, urlparse as _up

                        qs = parse_qs(_up(href).query)
                        if qs.get("uddg"):
                            href = unquote(qs["uddg"][0])
                    if not href.startswith("http") or "duckduckgo.com" in href:
                        continue
                    items.append(
                        {"title": title, "href": href, "cite": "", "url": href}
                    )
                    if len(items) >= 25:
                        break
                if not items:
                    # alternate markup
                    for m in re.finditer(
                        r'href="(https?://[^"]+)"[^>]*class="[^"]*result__a',
                        html,
                        re.I,
                    ):
                        href = m.group(1)
                        if "duckduckgo.com" in href:
                            continue
                        items.append(
                            {"title": "", "href": href, "cite": "", "url": href}
                        )
                        if len(items) >= 25:
                            break
                return items, False
        except Exception as e:
            logger.debug("DDG http listing failed: %s", e)
            return [], True

    async def _scrape_bing_safe(self, *args, **kwargs):
        """Back-compat alias."""
        return await self._scrape_search_engines_with_recovery(*args, **kwargs)

    async def _scrape_company_site(
        self, page: Page, url: str, title: str, domain: str, role: str, location: str
    ) -> ScrapedContact | None:
        try:
            page_emails, (phone_raw, phone_e164, is_valid) = await self._scrape_site_for_emails(
                page, url, location
            )
            emails = merge_page_and_fallback_emails(page_emails, domain)
            if not emails:
                return None
            body = ""
            try:
                body = await page.inner_text("body") if await page.query_selector("body") else ""
            except Exception:
                pass
            names = self._extract_names(body, role)
            first, last = sanitize_person_name(names.get("first"), names.get("last"))
            return ScrapedContact(
                first_name=first,
                last_name=last,
                company_name=self._clean_title(title),
                domain=domain,
                job_title=role,
                role_category=role,
                emails=emails,
                phone_raw=phone_raw,
                phone_e164=phone_e164,
                is_phone_valid=is_valid,
                source_url=url,
                location=location,
            )
        except Exception as e:
            logger.debug("Site scrape failed %s: %s", url, e)
            return None

    def _is_valid_domain(self, domain: str) -> bool:
        domain = registrable_domain(domain) or (domain or "").lower().replace("www.", "")
        if not domain or "." not in domain:
            return False
        if domain.endswith(".edu") or domain.endswith(".edu.cn") or domain.endswith(".ac.in"):
            return False
        return not is_blocked_host(domain)

    def _role_emails(self, domain: str, role: str) -> list[str]:
        prefixes = ROLE_EMAIL_PREFIXES.get(role, ["contact", "info", "hello"])
        domain = domain.lower().replace("www.", "")
        return [f"{p}@{domain}" for p in prefixes]

    def _resolve_bing_url(self, item: dict) -> str | None:
        href = item.get("href", "")
        decoded = self._decode_bing_url(href)
        if decoded.startswith("http") and "bing.com" not in decoded:
            return decoded
        cite = item.get("cite", "")
        if cite:
            host = cite.split()[0].split("/")[0]
            if "." in host and self._is_valid_domain(host):
                return f"https://{host}"
        return None

    @staticmethod
    def _decode_bing_url(href: str) -> str:
        match = re.search(r"[?&]u=a1([^&]+)", href)
        if match:
            try:
                padded = match.group(1) + "=" * (-len(match.group(1)) % 4)
                return base64.b64decode(padded).decode("utf-8", errors="ignore")
            except Exception:
                pass
        return href

    def _extract_names(self, text: str, role: str) -> dict:
        patterns = [
            rf"([A-Z][a-z]{{2,20}}(?:\s+[A-Z][a-z]{{2,20}})?)[,\s]+{re.escape(role)}",
            rf"{re.escape(role)}[:\s]+([A-Z][a-z]{{2,20}}(?:\s+[A-Z][a-z]{{2,20}})?)",
            r"(?:Founder|CEO|Co-founder|Co-Founder)[:\s]+([A-Z][a-z]{2,20}(?:\s+[A-Z][a-z]{2,20})?)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                parts = m.group(1).split()
                first = parts[0]
                last = parts[-1] if len(parts) > 1 else ""
                first, last = sanitize_person_name(first, last or None)
                if first:
                    return {"first": first, "last": last or ""}
        return {}

    def _parse_phone(self, text: str, region: str) -> tuple[str | None, str | None, bool]:
        region_code = "IN" if any(
            x in region.lower() for x in ["bangalore", "bengaluru", "delhi", "mumbai", "india", "pune", "hyderabad"]
        ) else "US"
        for match in phonenumbers.PhoneNumberMatcher(text, region_code):
            num = match.number
            if phonenumbers.is_possible_number(num):
                e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
                return match.raw_string, e164, phonenumbers.is_valid_number(num)
        return None, None, False

    def _clean_title(self, title: str) -> str:
        return re.sub(r"\s*[\|\-–—].*$", "", title).strip() or title
