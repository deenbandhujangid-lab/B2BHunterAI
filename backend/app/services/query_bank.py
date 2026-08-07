"""Generate and manage 1000+ search queries for company discovery."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.search_query import QueryStatus, SearchQuery

logger = logging.getLogger(__name__)

# Niches expand IT/software jobs into many long-tail queries
IT_NICHES = [
    "SaaS", "AI", "artificial intelligence", "machine learning", "fintech",
    "ERP", "CRM", "cloud", "cloud computing", "cybersecurity", "cyber security",
    "data analytics", "big data", "mobile app development", "software development",
    "web development", "IT services", "software", "healthcare software",
    "HR software", "logistics software", "edtech", "devops", "blockchain",
    "IoT", "automation", "RPA", "product engineering", "outsourcing",
    "staffing IT", "digital marketing software", "payment gateway",
    "accounting software", "inventory software", "e-commerce software",
    "gaming studio", "semiconductor software", "embedded systems",
    "QA testing", "UI UX agency", "IT consulting", "managed services",
    "BPO software", "telecom software", "retail tech", "proptech",
    "insurtech", "agritech", "healthtech", "martech", "legaltech",
]

TEMPLATES = [
    "{niche} company {city}",
    "{niche} startup {city}",
    "{niche} software company {city}",
    "{niche} services {city}",
    "{niche} firms in {city}",
    "best {niche} company {city}",
    "{niche} product company {city}",
    "{city} {niche} company",
    "{city} {niche} startup",
    "{niche} agency {city}",
    "{niche} solutions {city}",
    # Size mix — small / mid / local (not only MNC lists)
    "small {niche} company {city}",
    "SME {niche} {city}",
    "{niche} Pvt Ltd {city}",
    "local {niche} company {city}",
]

# Explicit size tiers — Startup / Mid / Large (primary for SerpAPI Google volume)
# High-yield LIST queries first → 1 Serp credit → listicle pages → many domains
SIZE_TIER_TEMPLATES = [
    # High-yield lists (aim: scrape 50+ company links from result pages)
    "top 50 startups in {city}",
    "top 50 companies in {city}",
    "list of startups in {city}",
    "list of companies in {city}",
    "100 startups {city}",
    "startup directory {city}",
    "company directory {city}",
    "top IT companies in {city} list",
    "best startups in {city} 2024",
    "best startups in {city} 2025",
    "top {industry} companies in {city} list",
    "famous companies in {city} with website",
    # Startup
    "startup companies {city}",
    "tech startups {city}",
    "{industry} startup {city}",
    "early stage startup {city}",
    "seed stage startup {city}",
    "new startups in {city}",
    "startup list {city}",
    "top startups {city}",
    "SaaS startups {city}",
    "IT startups {city}",
    # Mid
    "mid size companies {city}",
    "medium size companies {city}",
    "mid market company {city}",
    "growing companies {city}",
    "mid size {industry} company {city}",
    "scaleup companies {city}",
    "mid size IT company {city}",
    "emerging mid size companies {city}",
    # Large
    "large companies {city}",
    "MNC companies {city}",
    "Fortune companies {city}",
    "enterprise companies {city}",
    "big {industry} company {city}",
    "top {industry} companies {city}",
    "listed companies {city}",
    "largest companies {city}",
    "top MNC in {city}",
    "enterprise IT companies {city}",
]

# Explicit size / local discovery (works with any industry)
SIZE_EXTRAS = [
    "small business {city}",
    "SME companies {city}",
    "MSME companies {city}",
    "startup companies {city}",
    "mid size company {city}",
    "large companies {city}",
    "MNC {city}",
    "enterprise company {city}",
    "Pvt Ltd companies {city}",
    "LLP companies {city}",
    "local companies {city}",
    "emerging companies {city}",
    "bootstrapped startup {city}",
    "B2B company {city}",
    "company list {city}",
    "companies in {city}",
    "top companies {city}",
    "new startups {city}",
    "early stage startup {city}",
    "family business {city}",
    "trading company {city}",
    "manufacturing SME {city}",
    "service company {city}",
]

INDUSTRY_NICHES: dict[str, list[str]] = {
    "software": IT_NICHES,
    "it": IT_NICHES,
    "information technology": IT_NICHES,
    "technology": IT_NICHES,
    "saas": [n for n in IT_NICHES if "saas" in n.lower() or "software" in n.lower()] + IT_NICHES[:20],
    "corporate": [
        # Large
        "corporate", "MNC", "conglomerate",
        # Mid / services
        "business services", "consulting", "professional services", "B2B services",
        # Small / local / SME (Google should return chhoti + badi dono)
        "SME", "MSME", "small business", "startup", "local business",
        "Pvt Ltd", "mid size company", "emerging company",
        "IT services", "software services", "outsourcing",
    ] + IT_NICHES[:20],
    "pharma": [
        "pharma", "pharmaceutical", "biotech", "life sciences",
        "healthcare", "medical devices", "clinical research",
        "API manufacturer", "formulation", "diagnostics",
        "SME pharma", "pharma Pvt Ltd", "local pharma company",
    ],
    "fintech": [
        "fintech", "payments", "neobank", "lending", "wealth tech",
        "insurance tech", "trading platform", "UPI", "NBFC tech",
        "fintech startup", "fintech SME",
    ] + IT_NICHES[:10],
}


def _city_variants(location: str) -> list[str]:
    loc = (location or "").strip()
    low = loc.lower()
    cities = [loc]
    if "bangalore" in low or "bengaluru" in low:
        cities = ["Bangalore", "Bengaluru"]
    elif "delhi" in low:
        cities = ["Delhi", "New Delhi", "NCR"]
    elif "mumbai" in low or "bombay" in low:
        cities = ["Mumbai", "Bombay"]
    elif "gurgaon" in low or "gurugram" in low:
        cities = ["Gurgaon", "Gurugram"]
    elif "hyderabad" in low or "hyd" == low:
        # Area variants surface local + SME sites, not only big MNC HQ pages
        cities = [
            "Hyderabad",
            "Secunderabad",
            "Hitech City Hyderabad",
            "Gachibowli",
            "Madhapur",
            "Hitec City",
        ]
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for c in cities:
        k = c.lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _niches_for_industry(industry: str) -> list[str]:
    key = (industry or "software").strip().lower()
    for k, niches in INDUSTRY_NICHES.items():
        if k in key or key in k:
            return niches
    # Generic: industry name + IT niches for volume
    base = [industry.strip(), f"{industry.strip()} company", f"{industry.strip()} services"]
    return base + IT_NICHES


def generate_query_strings(location: str, industry: str) -> list[str]:
    """Build 1000+ unique queries — Startup / Small / Mid / Large mix first."""
    cities = _city_variants(location)
    niches = _niches_for_industry(industry)
    industry_label = (industry or "corporate").strip()
    queries: list[str] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        q = " ".join(q.split()).strip()
        if not q:
            return
        key = q.lower()
        if key not in seen:
            seen.add(key)
            queries.append(q)

    # Size-tier queries FIRST so listing hits all labels early
    for city in cities:
        for tpl in SIZE_TIER_TEMPLATES:
            _add(tpl.format(city=city, industry=industry_label))

    for city in cities:
        for niche in niches:
            for tpl in TEMPLATES:
                _add(tpl.format(niche=niche, city=city))

        for tpl in SIZE_EXTRAS:
            _add(tpl.format(city=city))

        extras = [
            f"software company in {city}",
            f"IT services company {city}",
            f"SaaS company {city}",
            f"tech startup {city}",
            f"software development company {city}",
            f"product based company {city}",
            f"IT company list {city}",
            f"top software companies {city}",
            f"small IT company {city}",
            f"SME IT company {city}",
            f"mid size IT company {city}",
            f"large IT company {city}",
            f"software Pvt Ltd {city}",
            f"digital agency {city}",
            f"web development company {city}",
            f"BPO company {city}",
        ]
        for q in extras:
            _add(q)

    return queries


def infer_size_label(source_query: str | None) -> str | None:
    """Map Google query wording → Startup | Small | Mid | Large."""
    if not source_query:
        return None
    q = source_query.lower()
    if any(
        x in q
        for x in (
            "startup",
            "early stage",
            "seed stage",
            "bootstrapped",
            "new startup",
        )
    ):
        return "Startup"
    if any(
        x in q
        for x in (
            "small ",
            "small business",
            "sme",
            "msme",
            "pvt ltd",
            "local ",
            "family business",
            "llp",
        )
    ):
        return "Small"
    if any(
        x in q
        for x in (
            "mid size",
            "mid-size",
            "medium size",
            "mid market",
            "mid-market",
            "scaleup",
            "growing compan",
            "emerging",
        )
    ):
        return "Mid"
    if any(
        x in q
        for x in (
            "large ",
            "mnc",
            "enterprise",
            "conglomerate",
            "fortune",
            "listed compan",
            "top ",
            "best ",
            "big ",
        )
    ):
        return "Large"
    return None


async def ensure_queries_seeded(
    db: AsyncSession,
    location: str,
    industry: str,
) -> int:
    """Insert any missing query strings for location+industry."""
    strings = generate_query_strings(location, industry)
    all_existing = set(
        (await db.execute(select(SearchQuery.query))).scalars().all()
    )
    all_existing_l = {q.lower() for q in all_existing}

    inserted = 0
    batch: list[SearchQuery] = []
    for q in strings:
        if q.lower() in all_existing_l:
            continue
        all_existing_l.add(q.lower())
        batch.append(
            SearchQuery(
                query=q[:500],
                location=location,
                industry=industry,
                status=QueryStatus.PENDING,
            )
        )
        if len(batch) >= 200:
            db.add_all(batch)
            await db.flush()
            inserted += len(batch)
            batch = []

    if batch:
        db.add_all(batch)
        await db.flush()
        inserted += len(batch)

    if inserted:
        await db.commit()
        logger.info(
            "Seeded %d search queries for %s / %s (bank size=%d)",
            inserted, location, industry, len(strings),
        )
    return inserted


async def fetch_pending_queries(
    db: AsyncSession,
    *,
    location: str,
    industry: str,
    limit: int = 20,
) -> list[SearchQuery]:
    """Prefer Startup/Small/Mid/Large wording so size mix fills early."""
    from sqlalchemy import case, or_

    size_hit = or_(
        SearchQuery.query.ilike("%top 50%"),
        SearchQuery.query.ilike("%list of%"),
        SearchQuery.query.ilike("%directory%"),
        SearchQuery.query.ilike("%100 startup%"),
        SearchQuery.query.ilike("%startup%"),
        SearchQuery.query.ilike("%mid size%"),
        SearchQuery.query.ilike("%medium size%"),
        SearchQuery.query.ilike("%mid market%"),
        SearchQuery.query.ilike("%scaleup%"),
        SearchQuery.query.ilike("%large %"),
        SearchQuery.query.ilike("%largest %"),
        SearchQuery.query.ilike("%MNC%"),
        SearchQuery.query.ilike("%enterprise%"),
        SearchQuery.query.ilike("%Fortune%"),
    )
    priority = case((size_hit, 0), else_=1)
    result = await db.execute(
        select(SearchQuery)
        .where(
            SearchQuery.location == location,
            SearchQuery.industry == industry,
            SearchQuery.status == QueryStatus.PENDING,
        )
        .order_by(priority.asc(), SearchQuery.id.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def mark_query_completed(db: AsyncSession, query_id: int) -> None:
    await db.execute(
        update(SearchQuery)
        .where(SearchQuery.id == query_id)
        .values(status=QueryStatus.COMPLETED, completed_at=datetime.utcnow())
    )


async def count_queries(
    db: AsyncSession,
    *,
    location: str,
    industry: str,
    status: QueryStatus | None = None,
) -> int:
    q = select(func.count(SearchQuery.id)).where(
        SearchQuery.location == location,
        SearchQuery.industry == industry,
    )
    if status is not None:
        q = q.where(SearchQuery.status == status)
    return (await db.execute(q)).scalar() or 0
