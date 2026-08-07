from __future__ import annotations

# Founder covers CEO (ceo@ / founder@ map to Founder). Admin optional in UI.
VALID_ROLES = ("Founder", "HR", "Admin", "CMO")


def parse_job_roles(target_role: str) -> list[str]:
    """Parse comma-separated roles stored on a job (single role still works)."""
    if not target_role or not target_role.strip():
        return ["Founder"]
    parts = [p.strip() for p in target_role.replace("|", ",").split(",") if p.strip()]
    roles = [p for p in parts if p in VALID_ROLES]
    return roles or [target_role.strip()]


def format_job_roles(roles: list[str]) -> str:
    return ", ".join(roles)
