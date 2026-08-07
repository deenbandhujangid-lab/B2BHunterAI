"""Per-run session cache — fresh on Start/Restart/New job, preloaded from DB."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailStatus, Lead
from app.models.company_check import CompanyCheck
from app.services.data_quality import company_keys


@dataclass
class RunSession:
    emails: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    companies: set[str] = field(default_factory=set)

    def add_lead(self, email: str, domain: str | None, company: str | None) -> None:
        self.emails.add(email.lower())
        if domain:
            self.domains.add(domain.lower().replace("www.", ""))
        if company:
            self.companies.update(company_keys(company))

    def add_checked(self, domain: str | None, company: str | None) -> None:
        """Previously scraped domain — skip even if no VALID lead was saved."""
        if domain:
            self.domains.add(domain.lower().replace("www.", ""))
        if company:
            self.companies.update(company_keys(company))

    def has_email(self, email: str) -> bool:
        return email.lower() in self.emails

    def has_domain(self, domain: str) -> bool:
        if not domain:
            return False
        return domain.lower().replace("www.", "") in self.domains

    def has_company(self, name: str) -> bool:
        if not name:
            return False
        return bool(company_keys(name) & self.companies)


async def load_run_session_from_db(db: AsyncSession) -> RunSession:
    """Load VALID leads + previously checked companies from DB."""
    session = RunSession()
    result = await db.execute(
        select(Lead.email, Lead.domain, Lead.company_name).where(
            Lead.email_status == EmailStatus.VALID
        )
    )
    for email, domain, company in result.all():
        session.add_lead(email or "", domain, company)

    checked = await db.execute(
        select(CompanyCheck.domain, CompanyCheck.company_name)
    )
    for domain, company in checked.all():
        session.add_checked(domain, company)

    return session
