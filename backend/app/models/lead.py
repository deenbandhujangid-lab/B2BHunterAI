from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class EmailStatus(str, enum.Enum):
    VALID = "VALID"
    RISKY_CATCHALL = "RISKY_CATCHALL"
    INVALID = "INVALID"
    UNVERIFIED = "UNVERIFIED"


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("search_jobs.id"), nullable=False, index=True
    )
    first_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    email_status: Mapped[EmailStatus] = mapped_column(
        Enum(EmailStatus), default=EmailStatus.UNVERIFIED, index=True
    )
    phone_raw: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    phone_e164: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_phone_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    job: Mapped["SearchJob"] = relationship("SearchJob", back_populates="leads")  # noqa: F821
