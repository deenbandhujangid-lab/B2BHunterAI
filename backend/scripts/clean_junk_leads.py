"""One-off: remove junk first/last names from existing leads."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import AsyncSessionLocal

JUNK = (
    "years", "year", "ago", "month", "day", "week", "hour", "the", "and",
    "contact", "email", "founder", "founders", "company", "limited",
    "wikipedia", "page", "home", "team", "services", "technology",
)


async def main() -> None:
    placeholders = ",".join(f"'{w}'" for w in JUNK)
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(f"UPDATE leads SET first_name = NULL WHERE lower(first_name) IN ({placeholders})")
        )
        await db.execute(
            text(f"UPDATE leads SET last_name = NULL WHERE lower(last_name) IN ({placeholders})")
        )
        await db.commit()
    print("Cleaned junk names from leads table.")


if __name__ == "__main__":
    asyncio.run(main())
