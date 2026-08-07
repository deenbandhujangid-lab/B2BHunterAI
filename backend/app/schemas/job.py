from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class JobStatusEnum(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"


class TargetRoleEnum(str, Enum):
    Founder = "Founder"
    HR = "HR"
    Admin = "Admin"
    CMO = "CMO"


class SearchJobCreate(BaseModel):
    target_location: str = Field(..., examples=["Bangalore"])
    target_industry: str = Field(..., examples=["Corporate"])
    target_role: TargetRoleEnum | None = Field(None, examples=["Founder"])
    target_roles: list[TargetRoleEnum] | None = Field(None, examples=[["Founder", "HR"]])
    daily_target: int = Field(default=5000, ge=50, le=5000)
    job_name: str | None = None

    @model_validator(mode="after")
    def normalize_roles(self) -> "SearchJobCreate":
        if self.target_roles:
            if len(self.target_roles) == 0:
                raise ValueError("Select at least one role")
            return self
        if self.target_role:
            self.target_roles = [self.target_role]
            return self
        raise ValueError("target_roles or target_role is required")


class SearchJobResponse(BaseModel):
    id: int
    job_name: str
    target_location: str
    target_industry: str
    target_role: str
    daily_target: int
    status: JobStatusEnum
    total_found: int
    restart_count: int = 0
    last_error: str | None = None
    activity_message: str | None = None
    last_activity_at: datetime | None = None
    created_at: datetime
    # Company universe for this job's location+industry
    companies_total: int = 0
    companies_pending: int = 0
    companies_done: int = 0
    queries_total: int = 0
    queries_pending: int = 0
    queries_completed: int = 0
    listing: bool = False  # Load Companies in progress
    harvesting: bool = False  # Email harvest subprocess running

    model_config = {"from_attributes": True}

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):  # type: ignore[override]
        if hasattr(obj, "restart_count") and obj.restart_count is None:
            obj.restart_count = 0
        return super().model_validate(obj, *args, **kwargs)


class SearchJobListResponse(BaseModel):
    jobs: list[SearchJobResponse]
    total: int
