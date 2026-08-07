from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CompanyQueueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    company_name: Optional[str] = None
    website: Optional[str] = None
    location: Optional[str] = None
    industry: Optional[str] = None
    source: str = "search"
    size_label: Optional[str] = None
    status: str
    last_error: Optional[str] = None
    created_at: Optional[datetime] = None
    checked_at: Optional[datetime] = None
    scraped: bool = False  # True when status is done (or failed = attempted)


class CompanyQueueListResponse(BaseModel):
    companies: list[CompanyQueueItem]
    total: int
    page: int
    page_size: int
    pending: int = 0
    harvesting: int = 0
    done: int = 0
    failed: int = 0
    job_id: int
    job_name: str = ""
    location: str = ""
    industry: str = ""
