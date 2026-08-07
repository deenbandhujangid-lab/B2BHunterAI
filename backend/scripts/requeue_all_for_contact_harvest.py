"""Reset all scraped company_queue rows to pending for contact-page re-harvest."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "local_database.db"


def main() -> None:
    con = sqlite3.connect(str(DB), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    cur = con.cursor()

    cur.execute(
        "UPDATE company_queue SET status='pending', last_error=NULL, checked_at=NULL "
        "WHERE lower(status) IN ('done', 'failed', 'harvesting')"
    )
    print(f"company_queue -> pending: {cur.rowcount}")

    cur.execute("DELETE FROM company_checks")
    print(f"company_checks cleared: {cur.rowcount}")

    cur.execute(
        "UPDATE search_jobs SET status='PAUSED', harvest_pid=NULL, listing_pid=NULL, "
        "activity_message=? ",
        (
            "All companies re-queued for contact-page harvest — click Start",
        ),
    )
    print(f"jobs updated: {cur.rowcount}")

    cur.execute("SELECT status, COUNT(*) FROM company_queue GROUP BY status")
    for row in cur.fetchall():
        print(f"  {row[0]}={row[1]}")

    con.commit()
    con.close()
    print("done")


if __name__ == "__main__":
    main()
