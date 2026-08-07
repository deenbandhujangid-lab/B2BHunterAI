"""Expand 1 SerpAPI credit into many company domains (0 extra Serp cost).

Flow:
  SerpAPI → ~10 Google links (often list portals)
  → Playwright opens those list pages (free)
  → extract outbound company websites
  → dedupe + SKIP_DOMAINS filter
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse, unquote

from app.services.scraper import SKIP_DOMAINS, USER_AGENTS, is_blocked_host, registrable_domain

logger = logging.getLogger(__name__)

HREF_RE = re.compile(
    r"""href\s*=\s*["'](https?://[^"'#\s>]+|//[^"'#\s>]+|/[^"'#\s>]+)["']""",
    re.I,
)

LIST_HINTS = (
    "top ",
    "top-",
    "list of",
    "list-",
    "companies in",
    "startups in",
    "startup list",
    "company list",
    "best companies",
    "best startups",
    "directory",
    "100 ",
    "50 ",
    "25 ",
    "ranking",
    "leaderboard",
    "firms in",
    "agencies in",
    "to watch",
    "startups for",
)

# Never enqueue these as a company — only expand company links from them
LIST_PORTALS = (
    "startupblink.com",
    "builtin.com",
    "registerkaro.in",
    "goodfirms.co",
    "clutch.co",
    "glassdoor.com",
    "glassdoor.co.in",
    "ambitionbox.com",
    "naukri.com",
    "yourstory.com",
    "inc42.com",
    "techcrunch.com",
    "entrackr.com",
    "vccircle.com",
    "tracxn.com",
    "crunchbase.com",
    "scribd.com",
    "linkedin.com",
    "wikipedia.org",
    "medium.com",
    "forbes.com",
    "livemint.com",
    "economictimes",
    "indiatimes.com",
    "business-standard.com",
    "moneycontrol.com",
    "tofler.in",
    "zauba.com",
    "justdial.com",
    "indiamart.com",
)

EXTRA_SKIP = (
    "naukri.com",
    "shine.com",
    "monster.com",
    "foundit.in",
    "timesjobs.com",
    "cloudfront.net",
    "amazonaws.com",
    "googleusercontent.com",
    "gstatic.com",
    "googleapis.com",
    "w3.org",
    "schema.org",
    "fbcdn.net",
    "twimg.com",
    "ytimg.com",
    "doubleclick.net",
    "googletagmanager.com",
    "jsdelivr.net",
    "unpkg.com",
    "cloudflare.com",
    "fontawesome.com",
    "shopify.com",
    "myshopify.com",
    "wordpress.com",
    "blogspot.com",
    "blogger.com",
    "tumblr.com",
    "pinterest.com",
    "tiktok.com",
    "jotform.com",
    "maps.app.goo.gl",
    "goo.gl",
    "bit.ly",
    "gmpg.org",
    "wp.com",
    "gravatar.com",
    "list-manage.com",
    "breezy.hr",
    "semrush.com",
    "irgwc.com",
)

PATH_SKIP = (
    "/login",
    "/signup",
    "/register",
    "/cart",
    "/checkout",
    "/tag/",
    "/tags/",
    "/author/",
    "/category/",
    "/wp-admin",
    "/cdn-cgi",
    "/privacy",
    "/terms",
    "/cookie",
    "/auth/",
    "/jobs",
    "/benefits",
)

PROFILE_PATH_RE = re.compile(
    r"""href\s*=\s*["']([^"']*?/(?:company|companies|startup|unicorn|firm)/[^"'#?]+)["']""",
    re.I,
)


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def is_list_portal(url_or_host: str) -> bool:
    h = _host(url_or_host) if "://" in (url_or_host or "") else (url_or_host or "").lower()
    h = h.replace("www.", "")
    for p in LIST_PORTALS:
        if h == p or h.endswith("." + p):
            return True
    return False


def is_expandable_result(url: str, title: str = "") -> bool:
    host = _host(url)
    if not host:
        return False
    if is_list_portal(host):
        return True
    blob = f"{title} {url}".lower()
    return any(h in blob for h in LIST_HINTS)


def _domain_ok(domain: str, page_host: str) -> bool:
    d = registrable_domain(domain)
    if not d:
        return False
    page_root = registrable_domain(page_host) or (page_host or "").lower().replace(
        "www.", ""
    )
    if d == page_root:
        return False
    if d.startswith(".") or d.endswith(".") or ".." in d:
        return False
    if len(d) < 4:
        return False
    if d.endswith(".edu") or d.endswith(".edu.cn") or d.endswith(".ac.in"):
        return False
    if is_blocked_host(d, EXTRA_SKIP):
        return False
    if is_list_portal(d):
        return False
    if d.endswith(".gov.in") or d.endswith(".nic.in"):
        return False
    return True


def _name_from_domain(domain: str) -> str:
    d = registrable_domain(domain) or domain
    base = d.split(".")[0].replace("-", " ").replace("_", " ").strip()
    return base.title()[:120] if base else d


def extract_domains_from_html(html: str, page_url: str, limit: int = 80) -> list[dict]:
    """Pull unique company domains from page HTML (outbound https links)."""
    page_host = _host(page_url)
    seen: set[str] = set()
    out: list[dict] = []
    for m in HREF_RE.finditer(html or ""):
        raw = m.group(1).strip()
        if raw.startswith("//"):
            raw = "https:" + raw
        elif raw.startswith("/"):
            continue  # same-site path — handled via profile crawl
        try:
            raw = unquote(raw)
        except Exception:
            pass
        low = raw.lower()
        if any(p in low for p in PATH_SKIP):
            continue
        if low.endswith((".pdf", ".jpg", ".png", ".gif", ".svg", ".css", ".js", ".zip")):
            continue
        host = _host(raw)
        if not _domain_ok(host, page_host):
            continue
        root = registrable_domain(host)
        if not root or root in seen:
            continue
        seen.add(root)
        out.append(
            {
                "company_name": _name_from_domain(root),
                "domain": root,
                "website": f"https://{root}/",
                "source_url": raw[:1000],
            }
        )
        if len(out) >= limit:
            break
    return out


def _profile_urls(html: str, page_url: str, limit: int = 40) -> list[str]:
    page_host = _host(page_url)
    out: list[str] = []
    seen: set[str] = set()
    for m in PROFILE_PATH_RE.finditer(html or ""):
        raw = m.group(1).strip()
        abs_url = urljoin(page_url, raw)
        host = _host(abs_url)
        if host and host != page_host and not is_list_portal(host):
            continue  # external non-portal profiles not useful here
        key = abs_url.split("?")[0].rstrip("/").lower()
        if key in seen:
            continue
        # Skip job/benefit subpages
        if any(x in key for x in ("/jobs", "/benefits", "/reviews", "/salaries")):
            continue
        seen.add(key)
        out.append(abs_url.split("?")[0])
        if len(out) >= limit:
            break
    return out


async def expand_companies_with_page(
    page,
    url: str,
    *,
    limit: int = 60,
    deepen_profiles: int = 12,
) -> list[dict]:
    """Open a list page in Playwright and mine company domains."""
    if not url or not url.startswith("http"):
        return []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
        try:
            await page.wait_for_timeout(1200)
            for _ in range(4):
                await page.mouse.wheel(0, 1200)
                await page.wait_for_timeout(200)
        except Exception:
            pass
        html = await page.content()
        found = extract_domains_from_html(html, str(page.url), limit=limit)
        seen = {r["domain"] for r in found}

        # Builtin-style /company/slug pages — open a few for official websites
        if len(found) < limit and deepen_profiles > 0:
            profiles = _profile_urls(html, str(page.url), limit=deepen_profiles)
            for purl in profiles:
                if len(found) >= limit:
                    break
                try:
                    await page.goto(purl, wait_until="domcontentloaded", timeout=25000)
                    await page.wait_for_timeout(500)
                    phtml = await page.content()
                    more = extract_domains_from_html(phtml, str(page.url), limit=8)
                    for row in more:
                        if row["domain"] in seen:
                            continue
                        seen.add(row["domain"])
                        found.append(row)
                        if len(found) >= limit:
                            break
                except Exception:
                    continue
        return found[:limit]
    except Exception as e:
        logger.debug("Playwright list expand failed %s: %s", url[:80], e)
        return []


def city_list_portal_urls(location: str) -> list[str]:
    """High-yield list pages for a city — opened free (no Serp credit)."""
    loc = (location or "").strip().lower()
    if not loc:
        return []
    # normalize common city names for URL slugs
    slug = loc.replace(" ", "-")
    aliases = [slug]
    if "bangalore" in loc or "bengaluru" in loc:
        aliases = ["bangalore", "bengaluru"]
    elif "hyderabad" in loc:
        aliases = ["hyderabad"]
    elif "mumbai" in loc or "bombay" in loc:
        aliases = ["mumbai"]
    elif "delhi" in loc or "ncr" in loc:
        aliases = ["delhi", "new-delhi"]
    elif "chennai" in loc or "madras" in loc:
        aliases = ["chennai"]
    elif "pune" in loc:
        aliases = ["pune"]
    urls: list[str] = []
    for a in aliases:
        urls.extend(
            [
                f"https://www.startupblink.com/top-startups/{a}-in",
                f"https://builtin.com/companies/location/as/india/{a}",
                f"https://www.goodfirms.co/it-services/{a}",
                f"https://clutch.co/in/it-services/{a}",
            ]
        )
    # dedupe
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def expand_companies_from_url(
    url: str,
    *,
    limit: int = 60,
    timeout: float = 18.0,
) -> list[dict]:
    """httpx fallback (many list sites block bots — prefer Playwright)."""
    import httpx

    if not url or not url.startswith("http"):
        return []
    headers = {
        "User-Agent": USER_AGENTS[0] if USER_AGENTS else "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers=headers,
        ) as client:
            r = await client.get(url)
            if r.status_code >= 400:
                return []
            html = r.text or ""
            if len(html) > 2_500_000:
                html = html[:2_500_000]
            return extract_domains_from_html(html, str(r.url), limit=limit)
    except Exception as e:
        logger.debug("List expand httpx failed %s: %s", url[:80], e)
        return []
