"""Cache SerpAPI JSON responses — replay later with 0 credits."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SerpApiCache(Base):
    """One row per (query + engine + start offset) SerpAPI response."""

    __tablename__ = "serpapi_cache"
    __table_args__ = (
        UniqueConstraint(
            "query_key", "engine", "start_offset", name="ux_serpapi_cache_qes"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Normalized query (lower + collapsed spaces)
    query_key: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(40), nullable=False, default="google")
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Full SerpAPI JSON (minus api_key)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    # Convenience: organic count at save time
    organic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=func.now()
    )
