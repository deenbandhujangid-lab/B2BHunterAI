"""Reject junk names / guessed emails — scraped page data only."""

from __future__ import annotations

import re

_INVALID_NAME_WORDS = {
    "years", "year", "ago", "month", "months", "day", "days", "week", "weeks",
    "hour", "hours", "minute", "minutes", "second", "seconds", "time", "times",
    "the", "and", "for", "with", "from", "about", "contact", "email", "phone",
    "company", "limited", "ltd", "pvt", "private", "inc", "corp", "corporation",
    "wikipedia", "article", "edit", "section", "category", "references",
    "external", "links", "website", "official", "portal", "home", "page",
    "founder", "founders", "co", "chief", "executive", "officer", "director",
    "manager", "head", "team", "staff", "member", "board", "group", "services",
    "technology", "technologies", "solutions", "india", "indian", "global",
    "north", "south", "east", "west", "central", "new", "old", "first", "last",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "click", "here", "read", "more", "learn", "view", "see", "all", "rights",
    "reserved", "copyright", "privacy", "policy", "terms", "conditions",
}

_JUNK_EMAIL_LOCAL = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "postmaster", "abuse", "bounce", "test", "testing", "example", "sample",
    "placeholder", "null", "undefined", "username", "yourname", "name",
    "email", "user", "administrator", "webmaster", "newsletter", "subscribe",
    "unsubscribe", "notification", "notifications", "alert", "alerts",
}

_GUESSED_ROLE_PREFIXES = {
    "founder", "ceo", "cofounder", "co-founder", "hello", "contact", "info",
    "hr", "careers", "jobs", "people", "talent", "recruitment", "admin",
    "office", "support", "reception", "marketing", "cmo", "growth", "brand",
    "digital", "sales", "enquiry", "inquiry", "help", "team", "service",
}


def is_generic_role_email(email: str) -> bool:
    """True for contact@/ceo@/founder@ style locals (often auto-generated)."""
    local = email.lower().split("@")[0].strip()
    return local in _GUESSED_ROLE_PREFIXES

_COMPANY_SUFFIX_RE = re.compile(
    r"\b("
    r"incorporated|corporation|corp|company|co|limited|ltd|llc|llp|plc|"
    r"pvt|private|group|holdings|enterprises|solutions|technologies|technology|"
    r"services|international|global|india|indian"
    r")\b\.?",
    re.I,
)


def normalize_company_key(name: str | None) -> str:
    """Loose match key — 'Infosys Ltd' and 'Infosys Limited' → 'infosys'."""
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = _COMPANY_SUFFIX_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def company_keys(name: str | None) -> set[str]:
    """Raw + normalized keys for dedup sets."""
    if not name:
        return set()
    keys = {name.lower().strip()}
    norm = normalize_company_key(name)
    if norm:
        keys.add(norm)
    return keys


def is_valid_name_part(part: str | None) -> bool:
    if not part:
        return False
    part = part.strip()
    if len(part) < 2 or len(part) > 40:
        return False
    if not part.replace("-", "").replace("'", "").isalpha():
        return False
    if not part[0].isupper():
        return False
    if part.lower() in _INVALID_NAME_WORDS:
        return False
    if part.isupper() and len(part) > 3:
        return False
    return True


def sanitize_person_name(
    first: str | None, last: str | None
) -> tuple[str | None, str | None]:
    first = (first or "").strip() or None
    last = (last or "").strip() or None
    if first and not is_valid_name_part(first):
        first = None
    if last and not is_valid_name_part(last):
        last = None
    if first and last and first.lower() == last.lower():
        last = None
    return first, last


def is_scraped_email_acceptable(email: str) -> bool:
    email = email.lower().strip()
    if "@" not in email:
        return False
    local, domain = email.split("@", 1)
    if not local or not domain or "." not in domain:
        return False
    if local in _JUNK_EMAIL_LOCAL:
        return False
    # HTML entities / scrape artifacts (e.g. u003esourcing-enquiries@...)
    if local.startswith("u00") or "u003" in local or ";" in local or "<" in local:
        return False
    if any(x in local for x in ("noreply", "no-reply", "donotreply", "example")):
        return False
    if any(
        email.endswith(ext)
        for ext in (".png", ".jpg", ".gif", ".svg", ".webp", ".avif", ".jpeg")
    ):
        return False
    return True


def is_guessed_role_email(email: str) -> bool:
    local = email.lower().split("@")[0]
    if "." in local and local not in _GUESSED_ROLE_PREFIXES:
        return False
    return local in _GUESSED_ROLE_PREFIXES


def role_email_fallbacks(domain: str | None) -> list[str]:
    """Balanced free fallbacks — contact/info/hr/hello@company (MX checked on save)."""
    if not domain:
        return []
    domain = domain.lower().replace("www.", "").strip()
    if not domain or "." not in domain:
        return []
    try:
        from app.core.config import settings as _s
        if getattr(_s, "ROLE_EMAIL_FALLBACK", True) is False:
            return []
        prefixes = [p.strip().lower() for p in _s.ROLE_EMAIL_PREFIXES.split(",") if p.strip()]
    except Exception:
        prefixes = ["contact", "info", "hr", "hello"]
    return [f"{p}@{domain}" for p in prefixes]


def filter_scraped_emails(emails: list[str], *, page_found: set[str] | None = None) -> list[str]:
    page_found = page_found or set()
    page_found_l = {e.lower() for e in page_found}
    out: list[str] = []
    for e in emails:
        el = e.lower().strip()
        if not is_scraped_email_acceptable(el):
            continue
        if page_found_l and el not in page_found_l:
            if is_guessed_role_email(el):
                continue
            local = el.split("@")[0]
            if local in _GUESSED_ROLE_PREFIXES:
                continue
        if el not in out:
            out.append(el)
    return out


def merge_page_and_fallback_emails(
    page_emails: list[str] | set[str], domain: str | None
) -> list[str]:
    """Page emails only. Fallbacks disabled when ROLE_EMAIL_FALLBACK=False."""
    page_set = {e.lower().strip() for e in page_emails if e}
    page_list = filter_scraped_emails(list(page_set), page_found=page_set)
    if page_list:
        return page_list
    fallbacks = role_email_fallbacks(domain)
    if not fallbacks:
        return []
    return filter_scraped_emails(fallbacks, page_found=set(fallbacks))
