"""Read/write SerpAPI JSON cache so repeated queries cost 0 credits."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.serpapi_cache import SerpApiCache

logger = logging.getLogger(__name__)


def normalize_query_key(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())[:500]


def _cache_enabled() -> bool:
    return bool(getattr(settings, "SERPAPI_CACHE_ENABLED", True))


def _ttl_days() -> int:
    return max(0, int(getattr(settings, "SERPAPI_CACHE_TTL_DAYS", 0) or 0))


def items_from_serpapi_json(data: dict) -> list[dict]:
    """Extract listing items from a cached/live SerpAPI payload."""
    items: list[dict] = []
    seen: set[str] = set()
    for row in data.get("organic_results") or []:
        link = (row.get("link") or "").strip()
        if not link.startswith("http") or link in seen:
            continue
        seen.add(link)
        items.append(
            {
                "title": (row.get("title") or "")[:255],
                "href": link,
                "cite": "",
                "url": link,
            }
        )
    return items


async def get_cached_response(
    query: str,
    *,
    engine: str = "google",
    start_offset: int = 0,
) -> dict | None:
    if not _cache_enabled():
        return None
    key = normalize_query_key(query)
    if not key:
        return None
    try:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(SerpApiCache).where(
                        SerpApiCache.query_key == key,
                        SerpApiCache.engine == engine,
                        SerpApiCache.start_offset == int(start_offset),
                    )
                )
            ).scalar_one_or_none()
            if not row:
                return None
            ttl = _ttl_days()
            if ttl > 0 and row.created_at:
                age = datetime.utcnow() - row.created_at
                if age > timedelta(days=ttl):
                    logger.info(
                        "SerpAPI cache expired (%dd) for %s start=%s",
                        ttl,
                        key[:50],
                        start_offset,
                    )
                    return None
            try:
                data = json.loads(row.response_json)
            except json.JSONDecodeError:
                return None
            logger.info(
                "SerpAPI cache HIT — 0 credits (q=%s start=%s organic=%s)",
                key[:50],
                start_offset,
                row.organic_count,
            )
            return data
    except Exception as e:
        logger.debug("SerpAPI cache read failed: %s", e)
        return None


async def save_cached_response(
    query: str,
    data: dict,
    *,
    engine: str = "google",
    start_offset: int = 0,
) -> None:
    if not _cache_enabled() or not isinstance(data, dict):
        return
    key = normalize_query_key(query)
    if not key:
        return
    # Never persist secrets
    payload = dict(data)
    payload.pop("search_metadata", None)  # may contain request urls with key
    # Also strip nested request params if present
    try:
        sm = data.get("search_metadata")
        if isinstance(sm, dict):
            safe_sm = {
                k: v
                for k, v in sm.items()
                if k not in ("json_endpoint", "raw_html_file", "google_url")
                or "api_key" not in str(v).lower()
            }
            payload["search_metadata"] = safe_sm
    except Exception:
        pass

    organic_count = len(data.get("organic_results") or [])
    raw = json.dumps(payload, ensure_ascii=False)
    try:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(SerpApiCache).where(
                        SerpApiCache.query_key == key,
                        SerpApiCache.engine == engine,
                        SerpApiCache.start_offset == int(start_offset),
                    )
                )
            ).scalar_one_or_none()
            if row:
                row.response_json = raw
                row.organic_count = organic_count
                row.updated_at = datetime.utcnow()
            else:
                db.add(
                    SerpApiCache(
                        query_key=key,
                        engine=engine,
                        start_offset=int(start_offset),
                        response_json=raw,
                        organic_count=organic_count,
                    )
                )
            await db.commit()
            logger.info(
                "SerpAPI cache SAVE (q=%s start=%s organic=%s)",
                key[:50],
                start_offset,
                organic_count,
            )
    except Exception as e:
        logger.warning("SerpAPI cache save failed: %s", e)
