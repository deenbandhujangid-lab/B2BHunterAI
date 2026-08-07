from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class JobStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"


class TargetRole(str, enum.Enum):
    FOUNDER = "Founder"
    HR = "HR"
    ADMIN = "Admin"
    CMO = "CMO"


class SearchJob(Base):
    __tablename__ = "search_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_location: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_industry: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    daily_target: Mapped[int] = mapped_column(Integer, default=5000)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.RUNNING, index=True
    )
    total_found: Mapped[int] = mapped_column(Integer, default=0)
    total_verified: Mapped[int] = mapped_column(Integer, default=0)
    restart_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activity_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scrape_checkpoint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    harvest_pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    listing_pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    leads: Mapped[list["Lead"]] = relationship(  # noqa: F821
        "Lead", back_populates="job", cascade="all, delete-orphan"
    )
