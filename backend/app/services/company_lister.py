"""Phase 1 — query-bank listing: name+domain only (no email harvest)."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import random
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Page, async_playwright

from app.core.config import settings
from app.services.list_expander import (
    city_list_portal_urls,
    expand_companies_from_url,
    expand_companies_with_page,
    is_expandable_result,
    is_list_portal,
)
from app.services.location_sources import get_wiki_categories
from app.services.scraper import (
    USER_AGENTS,
    LocalScraper,
    _loop_supports_playwright,
)

logger = logging.getLogger(__name__)


@dataclass
class ListedCompany:
    company_name: str
    domain: str
    source: str  # wiki | search | csv
    website: str | None = None
    source_query: str | None = None
    source_url: str | None = None
    linkedin_url: str | None = None


class CompanyLister:
    """Query-bank + Wikipedia discovery: store domains only."""

    def __init__(self):
        self.scraper = LocalScraper()

    async def list_from_query_bank(
        self,
        queries: list[dict],
        on_company=None,
        on_query_done=None,
        on_activity=None,
        on_notice=None,
        should_stop=None,
        skip_domains: set[str] | None = None,
    ) -> int:
        """
        Process pending search queries (Bing/DDG).
        queries: [{id, query}, ...]
        Marks each query done via on_query_done(query_id) after processing.
        Returns companies newly emitted.
        """
        kwargs = dict(
            queries=queries,
            on_company=on_company,
            on_query_done=on_query_done,
            on_activity=on_activity,
            on_notice=on_notice,
            should_stop=should_stop,
            skip_domains=skip_domains,
        )
        if _loop_supports_playwright():
            try:
                return await self._list_queries_async(**kwargs)
            except NotImplementedError:
                logger.warning("Playwright NotImplementedError — Proactor thread fallback")

        return await self._via_proactor(self._list_queries_async, kwargs)

    async def list_companies(
        self,
        location: str,
        industry: str,
        role: str,
        list_target: int,
        on_company=None,
        on_activity=None,
        on_notice=None,
        should_stop=None,
        skip_domains: set[str] | None = None,
    ) -> int:
        """Legacy/wiki-only short filler — prefer list_from_query_bank."""
        kwargs = dict(
            location=location,
            industry=industry,
            role=role,
            list_target=list_target,
            on_company=on_company,
            on_activity=on_activity,
            on_notice=on_notice,
            should_stop=should_stop,
            skip_domains=skip_domains,
        )
        if _loop_supports_playwright():
            try:
                return await self._list_async(**kwargs)
            except NotImplementedError:
                logger.warning("Playwright NotImplementedError — Proactor thread fallback")

        return await self._via_proactor(self._list_async, kwargs)

    async def _via_proactor(self, coro_fn, kwargs) -> int:
        main_loop = asyncio.get_running_loop()
        on_company = kwargs.get("on_company")
        on_query_done = kwargs.get("on_query_done")
        on_activity = kwargs.get("on_activity")
        on_notice = kwargs.get("on_notice")
        should_stop = kwargs.get("should_stop")

        async def bridge_company(item: ListedCompany):
            if not on_company:
                return
            fut = asyncio.run_coroutine_threadsafe(on_company(item), main_loop)
            await asyncio.wrap_future(fut)

        async def bridge_query_done(query_id: int):
            if not on_query_done:
                return
            fut = asyncio.run_coroutine_threadsafe(on_query_done(query_id), main_loop)
            await asyncio.wrap_future(fut)

        async def bridge_activity(msg: str):
            if not on_activity:
                return
            fut = asyncio.run_coroutine_threadsafe(on_activity(msg), main_loop)
            await asyncio.wrap_future(fut)

        async def bridge_notice(msg: str):
            if not on_notice:
                return
            fut = asyncio.run_coroutine_threadsafe(on_notice(msg), main_loop)
            await asyncio.wrap_future(fut)

        def bridge_stop() -> bool:
            return bool(should_stop()) if should_stop else False

        thread_kwargs = {
            **kwargs,
            "on_company": bridge_company if on_company else None,
            "on_query_done": bridge_query_done if on_query_done else None,
            "on_activity": bridge_activity if on_activity else None,
            "on_notice": bridge_notice if on_notice else None,
            "should_stop": bridge_stop if should_stop else None,
        }

        def runner():
            if sys.platform.startswith("win"):
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(coro_fn(**thread_kwargs))
            finally:
                loop.close()

        return await asyncio.to_thread(runner)

    async def _list_queries_async(
        self,
        queries: list[dict],
        on_company=None,
        on_query_done=None,
        on_activity=None,
        on_notice=None,
        should_stop=None,
        skip_domains: set[str] | None = None,
    ) -> int:
        listed = 0
        seen: set[str] = set(skip_domains or ())

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
                # SerpAPI first (if key). On missing/invalid key → silent free engines (no crash).
                engines: list[tuple[str, object]] = []
                has_serpapi = bool((settings.SERPAPI_KEY or "").strip())
                if has_serpapi:
                    engines.append(("google-serpapi", "serpapi"))
                # Free / browser fallbacks — always available so listing never hard-fails
                use_fallbacks = True
                if (
                    settings.LISTING_SERPAPI_ONLY
                    and has_serpapi
                ):
                    # Prefer SerpAPI-only when key present; fallbacks still added as safety net
                    # but only used if SerpAPI soft-fails (blocked=True)
                    use_fallbacks = True
                if use_fallbacks:
                    if (settings.GOOGLE_CSE_API_KEY or "").strip() and (
                        settings.GOOGLE_CSE_CX or ""
                    ).strip():
                        engines.append(("google-cse", "cse"))
                    if settings.USE_DDG_HTTP_LISTING:
                        engines.append(("ddg-http", "http"))
                    engines.append(("bing", self.scraper._fetch_bing_results))
                    engines.append(("duckduckgo", self.scraper._fetch_ddg_results))
                    if settings.USE_GOOGLE_BROWSER_LISTING or settings.USE_GOOGLE_LISTING:
                        engines.append(
                            ("google-browser", self.scraper._fetch_google_results)
                        )
                # Absolute last resort
                if not engines:
                    engines.append(("ddg-http", "http"))
                    engines.append(("bing", self.scraper._fetch_bing_results))

                cooldown_left: dict[str, int] = {}
                last_google_at = 0.0
                # If LISTING_SERPAPI_ONLY and SerpAPI succeeds for a query, don't need others.
                    # Soft-fail path already tries next engine when blocked=True.
                # After a paid Serp hit, also merge free DDG results (0 Serp cost).
                free_amplify = True

                for qi, qrow in enumerate(queries, start=1):
                    if should_stop and should_stop():
                        break

                    query_id = qrow["id"]
                    query = qrow["query"]
                    location_hint = (qrow.get("location") or "").strip()
                    await self.scraper._notify(
                        on_activity,
                        f"Query {qi}/{len(queries)}: {query[:70]}…",
                    )

                    items: list | None = None
                    used_engine = ""
                    for engine_name, fetch_fn in engines:
                        if cooldown_left.get(engine_name, 0) > 0:
                            cooldown_left[engine_name] -= 1
                            continue

                        if engine_name == "google-browser" and last_google_at:
                            import time as _time

                            gap = float(settings.GOOGLE_QUERY_GAP_SEC)
                            wait_left = gap - (_time.monotonic() - last_google_at)
                            while wait_left > 0:
                                await self.scraper._notify(
                                    on_activity,
                                    f"Google browser gap {int(wait_left)}s…",
                                )
                                step = min(15.0, wait_left)
                                await asyncio.sleep(step)
                                wait_left = gap - (_time.monotonic() - last_google_at)

                        try:
                            if fetch_fn == "serpapi":
                                await self.scraper._notify(
                                    on_activity,
                                    f"Google via SerpAPI: {query[:50]}…",
                                )
                                items, blocked = await self.scraper._fetch_google_serpapi(
                                    query
                                )
                                if getattr(
                                    self.scraper, "_last_serpapi_from_cache", False
                                ):
                                    await self.scraper._notify(
                                        on_activity,
                                        f"SerpAPI cache HIT (0 credits): {query[:45]}…",
                                    )
                            elif fetch_fn == "cse":
                                await self.scraper._notify(
                                    on_activity,
                                    f"Google via CSE API: {query[:50]}…",
                                )
                                items, blocked = await self.scraper._fetch_google_cse(query)
                            elif fetch_fn == "http":
                                await self.scraper._notify(
                                    on_activity,
                                    f"DDG search: {query[:50]}…",
                                )
                                items, blocked = await self.scraper._fetch_ddg_http(query)
                            else:
                                await page.context.clear_cookies()
                                await page.set_extra_http_headers({
                                    "User-Agent": random.choice(USER_AGENTS),
                                    "Accept-Language": "en-IN,en;q=0.9",
                                })
                                items, blocked = await fetch_fn(page, query)
                                if engine_name == "google-browser":
                                    import time as _time

                                    last_google_at = _time.monotonic()
                        except Exception as e:
                            logger.warning("Engine %s failed: %s", engine_name, e)
                            items, blocked = [], True

                        if blocked:
                            cooldown_left[engine_name] = int(
                                settings.ENGINE_COOLDOWN_QUERIES
                            )
                            # Soft notice only — never raise / never stop the job
                            if engine_name != "google-serpapi":
                                await self.scraper._notify(
                                    on_notice,
                                    f"{engine_name} blocked — trying next…",
                                )
                                await asyncio.sleep(
                                    min(12.0, settings.BING_CAPTCHA_COOLDOWN / 4)
                                )
                            items = None
                            continue

                        # Got results (even empty list without block) — accept engine
                        if items is not None and not blocked:
                            if (
                                engine_name == "google-serpapi"
                                and getattr(
                                    self.scraper, "_last_serpapi_from_cache", False
                                )
                            ):
                                used_engine = "google-serpapi-cache"
                            else:
                                used_engine = engine_name
                            break
                        # Empty but not blocked: try next engine for more coverage
                        if not items:
                            items = None
                            continue
                        used_engine = engine_name
                        break

                    if items is None:
                        # All engines blocked this query — skip, keep batch alive
                        await self.scraper._notify(
                            on_activity,
                            f"No engine for query {qi} — skipping (batch continues)…",
                        )
                        if on_query_done:
                            await on_query_done(query_id)
                        await asyncio.sleep(settings.LISTING_QUERY_GAP_SEC)
                        continue

                    # 1 Serp credit → also pull free DDG links for same query (amplify)
                    if (
                        free_amplify
                        and used_engine == "google-serpapi"
                        and settings.USE_DDG_HTTP_LISTING
                    ):
                        try:
                            ddg_items, ddg_blocked = await self.scraper._fetch_ddg_http(
                                query
                            )
                            if not ddg_blocked and ddg_items:
                                seen_urls = {
                                    (it.get("url") or it.get("href") or "")
                                    for it in items
                                }
                                for dit in ddg_items:
                                    u = dit.get("url") or dit.get("href") or ""
                                    if u and u not in seen_urls:
                                        items.append(dit)
                                        seen_urls.add(u)
                                await self.scraper._notify(
                                    on_activity,
                                    f"Free DDG amplify +{len(ddg_items)} links (0 Serp)",
                                )
                        except Exception:
                            pass

                    try:
                        max_results = max(
                            int(settings.SEARCH_RESULTS_PER_QUERY or 25), 40
                        )
                        expand_target = max(
                            0, int(getattr(settings, "SERPAPI_EXPAND_TARGET", 50) or 0)
                        )
                        expand_pages = max(
                            0, int(getattr(settings, "SERPAPI_EXPAND_PAGES", 8) or 0)
                        )
                        expand_candidates: list[tuple[str, str]] = []

                        for item in (items or [])[:max_results]:
                            if should_stop and should_stop():
                                break
                            target_url = item.get("url") or self.scraper._resolve_bing_url(item)
                            if not target_url:
                                continue
                            domain = urlparse(target_url).netloc.replace("www.", "").lower()
                            if not domain:
                                continue
                            title_raw = (item.get("title") or "").strip()
                            # Queue list/media portals for free expand (0 Serp cost)
                            if (
                                expand_target > 0
                                and expand_pages > 0
                                and is_expandable_result(target_url, title_raw)
                            ):
                                expand_candidates.append((target_url, title_raw))

                            # Never enqueue list portals as a "company"
                            if is_list_portal(domain):
                                continue
                            if domain in seen:
                                continue
                            if not self.scraper._is_valid_domain(domain):
                                continue
                            # Skip LinkedIn profile/search pages (company LinkedIn saved separately)
                            linkedin_url = None
                            if "linkedin.com" in domain:
                                if "/company/" in target_url.lower():
                                    linkedin_url = target_url.split("?")[0][:500]
                                continue

                            title = self.scraper._clean_title(
                                title_raw or domain
                            )
                            website = f"https://{domain}/"
                            ok = await self._emit(
                                on_company,
                                on_activity,
                                ListedCompany(
                                    company_name=title or domain,
                                    domain=domain,
                                    source=used_engine or "search",
                                    website=website,
                                    source_query=query,
                                    source_url=target_url[:1000],
                                    linkedin_url=linkedin_url,
                                ),
                                seen,
                            )
                            if ok:
                                listed += 1

                        # Seed known city list portals once per batch (free — 0 Serp)
                        if expand_target > 0 and location_hint and qi == 1:
                            for portal in city_list_portal_urls(location_hint):
                                expand_candidates.append((portal, "city-portal"))
                        # Dedupe expand URLs preserve order
                        if expand_candidates:
                            _seen_e: set[str] = set()
                            _deduped: list[tuple[str, str]] = []
                            for eu, et in expand_candidates:
                                key = eu.split("?")[0].rstrip("/").lower()
                                if key in _seen_e:
                                    continue
                                _seen_e.add(key)
                                _deduped.append((eu, et))
                            expand_candidates = _deduped

                        # 1 Serp credit → open list pages → many company domains
                        if expand_target > 0 and expand_candidates and not (
                            should_stop and should_stop()
                        ):
                            got_from_expand = 0
                            await self.scraper._notify(
                                on_activity,
                                f"Expanding lists → target {expand_target} companies/query…",
                            )
                            for eurl, _etitle in expand_candidates[:expand_pages]:
                                if should_stop and should_stop():
                                    break
                                if got_from_expand >= expand_target:
                                    break
                                need = expand_target - got_from_expand
                                found = await expand_companies_with_page(
                                    page,
                                    eurl,
                                    limit=max(need, 25),
                                    deepen_profiles=15,
                                )
                                if not found:
                                    found = await expand_companies_from_url(
                                        eurl, limit=max(need, 20)
                                    )
                                for row in found:
                                    if got_from_expand >= expand_target:
                                        break
                                    if should_stop and should_stop():
                                        break
                                    ok = await self._emit(
                                        on_company,
                                        on_activity,
                                        ListedCompany(
                                            company_name=row["company_name"],
                                            domain=row["domain"],
                                            source=f"{used_engine or 'search'}-expand",
                                            website=row.get("website"),
                                            source_query=query,
                                            source_url=(row.get("source_url") or eurl)[
                                                :1000
                                            ],
                                        ),
                                        seen,
                                    )
                                    if ok:
                                        listed += 1
                                        got_from_expand += 1
                            if got_from_expand:
                                await self.scraper._notify(
                                    on_activity,
                                    f"List expand +{got_from_expand} companies (0 Serp credit)",
                                )

                        if on_query_done:
                            await on_query_done(query_id)

                        # SerpAPI/CSE can go faster; browser paths stay slower via gap above
                        base_gap = (
                            1.5
                            if used_engine
                            in ("google-serpapi", "google-serpapi-cache", "google-cse")
                            else settings.LISTING_QUERY_GAP_SEC
                        )
                        delay = random.uniform(base_gap, base_gap + 3)
                        await self.scraper._notify(
                            on_activity,
                            f"Query done via {used_engine or 'search'} — cooling {int(delay)}s…",
                        )
                        await asyncio.sleep(delay)
                    except Exception as e:
                        logger.warning("Query failed '%s': %s", query[:60], e)
                        if on_query_done:
                            await on_query_done(query_id)
                        await asyncio.sleep(settings.LISTING_QUERY_GAP_SEC)
            finally:
                try:
                    await browser.close()
                except Exception:
                    logger.debug("Browser close skipped", exc_info=True)

        logger.info("Query-bank listed %d new domains from %d queries", listed, len(queries))
        return listed

    async def _list_async(
        self,
        location: str,
        industry: str,
        role: str,
        list_target: int,
        on_company=None,
        on_activity=None,
        on_notice=None,
        should_stop=None,
        skip_domains: set[str] | None = None,
    ) -> int:
        listed = 0
        seen: set[str] = set(skip_domains or ())

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
                await self.scraper._notify(
                    on_activity,
                    f"Wiki filler for {location} / {industry}…",
                )
                listed += await self._list_wikipedia(
                    page, location, industry, list_target - listed,
                    seen, on_company, on_activity, should_stop,
                )
            finally:
                try:
                    await browser.close()
                except Exception:
                    logger.debug("Browser close skipped", exc_info=True)

        return listed

    async def _emit(
        self,
        on_company,
        on_activity,
        item: ListedCompany,
        seen: set[str],
    ) -> bool:
        domain = item.domain.lower().replace("www.", "").strip()
        # Collapse subdomains to registrable root before dedupe
        from app.services.scraper import registrable_domain

        domain = registrable_domain(domain) or domain
        if not domain or domain in seen or not self.scraper._is_valid_domain(domain):
            return False
        # Universities
        if domain.endswith(".edu") or domain.endswith(".edu.cn") or domain.endswith(".ac.in"):
            return False
        seen.add(domain)
        item.domain = domain
        if not item.website:
            item.website = f"https://{domain}/"
        if on_company:
            await on_company(item)
        await self.scraper._notify(
            on_activity,
            f"Listed {item.company_name[:40]} ({domain}) via {item.source}",
        )
        return True

    async def _list_wikipedia(
        self,
        page: Page,
        location: str,
        industry: str,
        limit: int,
        seen: set[str],
        on_company,
        on_activity,
        should_stop,
    ) -> int:
        added = 0
        # Short secondary pass — query bank is primary
        max_per_cat = min(40, limit)
        for category in get_wiki_categories(location, industry)[:4]:
            if should_stop and should_stop():
                break
            if added >= limit:
                break

            companies = await self.scraper._fetch_wiki_category_members(page, category)
            await self.scraper._notify(
                on_activity,
                f"Wiki list: {category} ({len(companies)} names)…",
            )

            for company in companies[:max_per_cat]:
                if should_stop and should_stop():
                    break
                if added >= limit:
                    break

                cname = company["name"]
                site = await self.scraper._fetch_official_website(page, cname)
                if not site:
                    await asyncio.sleep(0.3)
                    continue
                domain = urlparse(site).netloc.replace("www.", "").lower()
                ok = await self._emit(
                    on_company,
                    on_activity,
                    ListedCompany(
                        company_name=cname,
                        domain=domain,
                        source="wiki",
                        website=site,
                        source_query=category,
                        source_url=company.get("href"),
                    ),
                    seen,
                )
                if ok:
                    added += 1
                await asyncio.sleep(random.uniform(0.5, 1.2))

            await asyncio.sleep(settings.CATEGORY_PAUSE)

        return added


def parse_company_csv(text: str) -> list[ListedCompany]:
    """Optional CSV: columns domain[,company_name]."""
    out: list[ListedCompany] = []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return out
    fields = {f.lower().strip(): f for f in reader.fieldnames}
    domain_key = fields.get("domain") or fields.get("website") or fields.get("url")
    name_key = fields.get("company_name") or fields.get("name") or fields.get("company")
    if not domain_key:
        return out
    for row in reader:
        raw = (row.get(domain_key) or "").strip()
        if not raw:
            continue
        if "://" not in raw:
            raw = "https://" + raw
        domain = urlparse(raw).netloc.replace("www.", "").lower() or raw.lower().replace(
            "www.", ""
        )
        name = (row.get(name_key) or domain).strip() if name_key else domain
        if domain and "." in domain:
            out.append(
                ListedCompany(
                    company_name=name,
                    domain=domain,
                    source="csv",
                    website=f"https://{domain}/",
                )
            )
    return out
