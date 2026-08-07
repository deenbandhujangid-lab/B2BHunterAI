"""Reset specific domains to pending for contact-page re-harvest."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "local_database.db"
DOMAINS = ("freshworks.com", "tatacoffee.com")


def main() -> None:
    con = sqlite3.connect(str(DB), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    cur = con.cursor()
    for d in DOMAINS:
        cur.execute(
            "UPDATE company_queue SET status='pending', last_error=NULL, checked_at=NULL WHERE domain=?",
            (d,),
        )
        print(f"queue {d} -> pending ({cur.rowcount})")
        cur.execute("DELETE FROM company_checks WHERE domain=?", (d,))
        print(f"checks {d} deleted ({cur.rowcount})")
    cur.execute(
        "UPDATE search_jobs SET status='PAUSED', harvest_pid=NULL, "
        "activity_message=? WHERE id=2",
        ("Contact pages fixed — click Start (Freshworks/Tata Coffee pending)",),
    )
    con.commit()
    con.close()
    print("done")


if __name__ == "__main__":
    main()
