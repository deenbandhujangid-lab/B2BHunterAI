"""Phase 2 — harvest emails from pending company_queue domains.

Uses httpx (hard timeouts) for homepage + contact/about pages.
Playwright is avoided here — it hangs on heavy sites (e.g. Tanishq).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings
from app.services.data_quality import (
    is_generic_role_email,
    merge_page_and_fallback_emails,
    sanitize_person_name,
)
from app.services.scraper import USER_AGENTS, LocalScraper, ScrapedContact

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.I,
)

_CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/contactus",
    "/contact/",
    "/about",
    "/about-us",
    "/aboutus",
    "/company/contact",
    "/company/contact-us",
    "/get-in-touch",
    "/reach-us",
    "/support",
    "/enquiry",
    "/inquire",
)

_CONTACT_HREF_KEYS = (
    "contact",
    "get-in-touch",
    "reach-us",
    "enquire",
    "inquiry",
    "about-us",
    "about",
)


class EmailHarvester:
    """Fetch each queued domain homepage + contact pages for role emails."""

    def __init__(self):
        self.scraper = LocalScraper()

    async def harvest_domains(
        self,
        items: list[dict],
        roles: list[str],
        location: str,
        on_contact=None,
        on_activity=None,
        on_domain_done=None,
        on_domain_failed=None,
        should_stop=None,
        skip_emails: set[str] | None = None,
        skip_domains: set[str] | None = None,
    ) -> int:
        known_emails = skip_emails if skip_emails is not None else set()
        known_domains = skip_domains if skip_domains is not None else set()
        processed = 0
        # Multi-page contact crawl needs a bit more than single homepage
        timeout = float(max(settings.DOMAIN_HARVEST_TIMEOUT, 35.0))

        limits = httpx.Limits(max_connections=6, max_keepalive_connections=3)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(12.0, connect=8.0),
            limits=limits,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-IN,en;q=0.9",
            },
            verify=False,
        ) as client:
            for item in items:
                if should_stop and should_stop():
                    break

                queue_id = item["id"]
                domain = (item.get("domain") or "").lower().replace("www.", "")
                company_name = item.get("company_name") or domain

                if not domain or not self.scraper._is_valid_domain(domain):
                    if on_domain_failed:
                        await on_domain_failed(queue_id, "invalid domain")
                    processed += 1
                    continue

                if domain in known_domains:
                    if on_domain_done:
                        await on_domain_done(queue_id, domain, company_name)
                    processed += 1
                    continue

                await self._notify(
                    on_activity,
                    f"Harvesting {company_name[:40]} ({domain})…",
                )

                try:
                    contacts = await asyncio.wait_for(
                        self._harvest_one_http(
                            client,
                            domain,
                            company_name,
                            roles or ["Founder"],
                            location,
                            known_emails,
                        ),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning("HTTP harvest timeout %s — no fallback", domain)
                    contacts = []
                    await self._notify(
                        on_activity, f"Timed out {domain} — skip (no email)"
                    )
                except Exception as e:
                    logger.warning("Harvest failed %s: %s", domain, e)
                    contacts = []

                if contacts:
                    for contact in contacts:
                        if on_contact:
                            try:
                                await asyncio.wait_for(on_contact(contact), timeout=12.0)
                            except asyncio.TimeoutError:
                                logger.warning("Save timeout %s", domain)
                            for e in contact.emails:
                                known_emails.add(e.lower())
                else:
                    await self._notify(on_activity, f"No email on {domain} — done")

                known_domains.add(domain)
                if on_domain_done:
                    await on_domain_done(queue_id, domain, company_name)

                processed += 1
                await asyncio.sleep(random.uniform(0.4, 1.2))

        return processed

    async def _notify(self, cb, msg: str) -> None:
        if not cb:
            return
        try:
            await cb(msg)
        except Exception:
            pass

    async def _harvest_one_http(
        self,
        client: httpx.AsyncClient,
        domain: str,
        company_name: str,
        roles: list[str],
        location: str,
        known_emails: set[str],
    ) -> list[ScrapedContact]:
        page_emails: set[str] = set()
        mailto_emails: set[str] = set()
        home = f"https://{domain}/"
        html_blob = ""

        home_html = ""
        for url in (f"https://{domain}/", f"http://{domain}/"):
            try:
                r = await client.get(url)
                if r.status_code >= 400:
                    continue
                home_html = r.text or ""
                home = str(r.url)
                html_blob = home_html
                mailto, body = self._emails_from_html(home_html, domain)
                mailto_emails.update(mailto)
                page_emails.update(mailto | body)
                break
            except Exception as e:
                logger.debug("Homepage fail %s: %s", url, e)

        # Always try contact/about pages — emails often only live there
        contact_urls = self._contact_urls(home, home_html)
        for curl in contact_urls:
            try:
                r = await client.get(curl)
                if r.status_code >= 400:
                    continue
                page_html = r.text or ""
                html_blob = page_html or html_blob
                mailto, body = self._emails_from_html(page_html, domain)
                mailto_emails.update(mailto)
                page_emails.update(mailto | body)
                # Enough signal — stop early
                if len(mailto_emails) >= 2 or len(page_emails) >= 5:
                    break
            except Exception as e:
                logger.debug("Contact page fail %s: %s", curl, e)
                continue

        # Prefer mailto; allow non-generic body emails; keep mailto even if role-like
        if not settings.ROLE_EMAIL_FALLBACK:
            page_emails = {
                e
                for e in page_emails
                if (e in mailto_emails) or (not is_generic_role_email(e))
            }

        emails = merge_page_and_fallback_emails(page_emails, domain)
        emails = [e for e in emails if e.lower() not in known_emails]
        if not settings.ROLE_EMAIL_FALLBACK:
            emails = [
                e
                for e in emails
                if (e.lower() in {m.lower() for m in mailto_emails})
                or (not is_generic_role_email(e))
            ]
        if not emails:
            return []

        by_role = self._assign_emails_to_roles(emails, roles or ["Founder"])
        out: list[ScrapedContact] = []
        for role, role_emails in by_role.items():
            if not role_emails:
                continue
            names = (
                self.scraper._extract_names(html_blob[:8000], role) if html_blob else {}
            )
            first, last = sanitize_person_name(names.get("first"), names.get("last"))
            out.append(
                ScrapedContact(
                    first_name=first,
                    last_name=last,
                    company_name=company_name,
                    domain=domain,
                    job_title=role,
                    role_category=role,
                    emails=list(role_emails),
                    phone_raw=None,
                    phone_e164=None,
                    is_phone_valid=False,
                    source_url=home,
                    location=location,
                )
            )
        return out

    def _contact_urls(self, home: str, home_html: str) -> list[str]:
        """Common contact paths + same-site links from homepage HTML."""
        base = home if home.endswith("/") else home + "/"
        parsed_home = urlparse(home)
        home_host = (parsed_home.netloc or "").lower().replace("www.", "")
        out: list[str] = []
        seen: set[str] = set()

        def add(u: str) -> None:
            key = u.rstrip("/").lower()
            if key in seen:
                return
            seen.add(key)
            out.append(u)

        for p in _CONTACT_PATHS:
            add(urljoin(base, p.lstrip("/")))

        # Parse <a href> for contact-like links (same host only)
        for m in re.finditer(
            r"""href=["']([^"']+)["'][^>]*>([^<]{0,80})""", home_html or "", re.I
        ):
            href, text = m.group(1), (m.group(2) or "").lower()
            blob = f"{href} {text}".lower()
            if not any(k in blob for k in _CONTACT_HREF_KEYS):
                continue
            abs_url = urljoin(base, href)
            host = urlparse(abs_url).netloc.lower().replace("www.", "")
            if host and home_host and host != home_host and not host.endswith("." + home_host):
                continue
            if abs_url.startswith("http"):
                add(abs_url)

        return out[:8]

    def _emails_from_html(self, html: str, domain: str) -> tuple[set[str], set[str]]:
        """Return (mailto_emails, body_regex_emails)."""
        mailto: set[str] = set()
        body: set[str] = set()
        for m in re.finditer(
            r"mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})", html, re.I
        ):
            mailto.add(m.group(1).lower().rstrip("."))
        for m in _EMAIL_RE.finditer(html):
            e = m.group(0).lower().rstrip(".")
            if e.endswith(
                (".png", ".jpg", ".gif", ".svg", ".webp", ".js", ".css", ".avif")
            ):
                continue
            if re.fullmatch(r"[a-f0-9]{20,}@.+", e):
                continue
            body.add(e)

        root = domain.lower().replace("www.", "")
        root_base = root.split(".")[0] if root else ""

        def keep(e: str) -> bool:
            host = e.split("@")[-1]
            if host == root or host.endswith("." + root):
                return True
            # Parent brand domains (tatacoffee → tataconsumer.com, etc.)
            if root_base and len(root_base) >= 4 and root_base in host:
                return True
            return False

        def filter_set(s: set[str]) -> set[str]:
            c = {e for e in s if keep(e)}
            return c if c else s

        return filter_set(mailto), filter_set(body)

    def _assign_emails_to_roles(
        self, emails: list[str], roles: list[str]
    ) -> dict[str, list[str]]:
        prefixes = {
            "Founder": (
                "founder",
                "ceo",
                "cofounder",
                "co-founder",
                "owner",
                "chiefexecutive",
                "managingdirector",
                "md",
            ),
            "HR": ("hr", "careers", "jobs", "people", "talent", "recruit"),
            "Admin": ("admin", "office", "support", "reception", "sales"),
            "CMO": ("marketing", "cmo", "growth", "brand", "digital"),
        }
        assigned: dict[str, list[str]] = {r: [] for r in roles}
        used: set[str] = set()
        for e in emails:
            local = (
                e.split("@")[0]
                .lower()
                .replace(".", "")
                .replace("_", "")
                .replace("-", "")
            )
            matched = None
            if "Founder" in roles and (
                local.startswith("ceo")
                or "ceo" in local
                or local in ("md", "managingdirector")
            ):
                matched = "Founder"
            else:
                for role in roles:
                    if any(p in local for p in prefixes.get(role, ())):
                        matched = role
                        break
            if matched:
                assigned[matched].append(e)
                used.add(e.lower())
        leftover = [e for e in emails if e.lower() not in used]
        if leftover and roles:
            assigned[roles[0]].extend(leftover)
        return assigned
