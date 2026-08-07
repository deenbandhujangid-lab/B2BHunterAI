from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class EmailStatusEnum(str, Enum):
    VALID = "VALID"
    RISKY_CATCHALL = "RISKY_CATCHALL"
    INVALID = "INVALID"
    UNVERIFIED = "UNVERIFIED"


class LeadResponse(BaseModel):
    id: int
    job_id: int
    first_name: str | None
    last_name: str | None
    job_title: str | None
    role_category: str | None
    company_name: str | None
    domain: str | None
    email: str
    email_status: EmailStatusEnum
    phone_raw: str | None
    phone_e164: str | None
    is_phone_valid: bool
    source_url: str | None
    target_location: str | None = None
    target_industry: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadListResponse(BaseModel):
    leads: list[LeadResponse]
    total: int
    page: int
    page_size: int


class DashboardStats(BaseModel):
    total_extracted: int
    active_jobs: int
    total_jobs: int
    paused_jobs: int
