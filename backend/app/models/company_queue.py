"""Company universe queue — Phase 1 lists domains; Phase 2 harvests emails."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class QueueStatus(str, enum.Enum):
    PENDING = "pending"
    HARVESTING = "harvesting"
    DONE = "done"
    FAILED = "failed"


class CompanyQueue(Base):
    __tablename__ = "company_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="search")
    source_query: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    size_label: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    job_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("search_jobs.id"), nullable=True, index=True
    )
    status: Mapped[QueueStatus] = mapped_column(
        Enum(
            QueueStatus,
            native_enum=False,
            length=20,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=QueueStatus.PENDING,
        index=True,
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
