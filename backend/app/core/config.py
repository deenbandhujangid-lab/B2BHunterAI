from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DB_PATH = (_BASE_DIR / "local_database.db").resolve()


class Settings(BaseSettings):
    APP_NAME: str = "B2B Hunter AI"
    APP_VERSION: str = "2.1.0"
    DEBUG: bool = True
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8002

    # Absolute path — same file for uvicorn + DB Browser
    DATABASE_URL: str = f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}"

    SCRAPER_HEADLESS: bool = True
    SCRAPER_TIMEOUT_MS: int = 30000
    SCRAPER_MAX_WIKI_PAGES: int = 20
    SCRAPER_MAX_COMPANIES_PER_CAT: int = 800
    SCRAPER_MAX_BING_QUERIES: int = 10
    # Always run search discovery after Wikipedia (SMEs not on Wiki)
    ALWAYS_RUN_SEARCH_DISCOVERY: bool = True
    # If page has no email, do NOT invent contact@/ceo@/founder@ etc.
    ROLE_EMAIL_FALLBACK: bool = False
    ROLE_EMAIL_PREFIXES: str = "contact,info,hr,hello,founder,ceo,sales,support"

    # Anti-block intervals (seconds) — tuned for higher daily volume
    SCRAPE_DELAY_MIN: float = 4.0
    SCRAPE_DELAY_MAX: float = 8.0
    BING_EXTRA_DELAY: float = 12.0
    BING_CAPTCHA_COOLDOWN: float = 90.0
    SAFETY_PAUSE_EVERY: int = 30
    SAFETY_PAUSE_MIN: float = 15.0
    SAFETY_PAUSE_MAX: float = 30.0
    CATEGORY_PAUSE: float = 3.0
    SKIP_SEARCH_ENGINES: bool = False

    MAX_LEADS_PER_JOB: int = 5000
    # Phase 1 lists this many domains per lead target before Phase 2 harvest
    QUEUE_LIST_MULTIPLIER: float = 2.0
    # Create + Load More: target this many NEW domains per list run
    COMPANIES_PER_LOAD: int = 800
    # Query-bank Phase 1 caps (enough queries to approach COMPANIES_PER_LOAD)
    QUERIES_PER_RUN: int = 30
    # While Load More runs, do not auto-start email harvest (list companies first)
    LIST_FIRST_HOLD_HARVEST: bool = True
    SEARCH_RESULTS_PER_QUERY: int = 25
    QUERY_DELAY_MIN: float = 8.0
    QUERY_DELAY_MAX: float = 20.0
    # Per-domain email harvest hard stop (heavy sites like Tanishq)
    DOMAIN_HARVEST_TIMEOUT: float = 40.0
    # Google at scale = API (no CAPTCHA). Browser Google is slow + CAPTCHA-prone.
    # SerpAPI: https://serpapi.com — set SERPAPI_KEY for high-volume Google results
    SERPAPI_KEY: str = ""
    # Prefer SerpAPI when key works; on missing/invalid key → silent free fallback (no crash)
    LISTING_SERPAPI_ONLY: bool = False
    # Each page ≈ 1 SerpAPI credit (~10 Google links). Keep at 1 — expand lists for volume.
    SERPAPI_PAGES: int = 1
    # Stop extra pages when a page returns fewer than this many organic hits
    SERPAPI_MIN_RESULTS_CONTINUE: int = 4
    # Save SerpAPI JSON in DB; same query → internal replay (0 credits)
    SERPAPI_CACHE_ENABLED: bool = True
    # 0 = keep forever; >0 = re-fetch after N days
    SERPAPI_CACHE_TTL_DAYS: int = 0
    # After 1 Serp hit: scrape list/media pages (free) aiming for this many companies/query
    SERPAPI_EXPAND_TARGET: int = 50
    # Max list/directory URLs to open per SerpAPI query (0 Serp cost)
    SERPAPI_EXPAND_PAGES: int = 10
    # Google Programmable Search (Custom Search JSON API)
    GOOGLE_CSE_API_KEY: str = ""
    GOOGLE_CSE_CX: str = ""
    # Browser Google scrape (Playwright) — only if you accept CAPTCHA risk
    USE_GOOGLE_BROWSER_LISTING: bool = False
    GOOGLE_QUERY_GAP_SEC: float = 180.0
    # Free fallback when SerpAPI key missing/invalid
    USE_DDG_HTTP_LISTING: bool = True
    LISTING_QUERY_GAP_SEC: float = 3.0
    ENGINE_COOLDOWN_QUERIES: int = 8
    # legacy alias
    USE_GOOGLE_LISTING: bool = False

    SMTP_VERIFY_TIMEOUT: int = 12
    SMTP_FROM_EMAIL: str = "verify@localhost.local"
    MX_LOOKUP_TIMEOUT: int = 5

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ]

    BASE_DIR: Path = _BASE_DIR

    class Config:
        # Project root .env + backend/.env
        env_file = (
            str(_BASE_DIR.parent / ".env"),
            str(_BASE_DIR / ".env"),
            ".env",
        )


settings = Settings()
