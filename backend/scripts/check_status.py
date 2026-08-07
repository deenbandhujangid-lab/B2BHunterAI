import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parent.parent / "local_database.db"
c = sqlite3.connect(str(db))
print("queue:", c.execute("SELECT status, COUNT(1) FROM company_queue GROUP BY status").fetchall())
print("fw:", c.execute("SELECT status FROM company_queue WHERE domain='freshworks.com'").fetchone())
print(
    "tata:",
    c.execute(
        "SELECT domain, status FROM company_queue WHERE domain LIKE '%tata%' LIMIT 10"
    ).fetchall(),
)
print("leads:", c.execute("SELECT COUNT(1) FROM leads").fetchone()[0])
cols = [r[1] for r in c.execute("PRAGMA table_info(leads)").fetchall()]
print("lead cols:", cols)
email_col = "email" if "email" in cols else cols[0]
dom_col = next((x for x in ("domain", "company_domain", "company") if x in cols), None)
q = f"SELECT {email_col}" + (f", {dom_col}" if dom_col else "") + " FROM leads ORDER BY id DESC LIMIT 8"
print("recent leads:", c.execute(q).fetchall())
print(
    "tata lead:",
    c.execute("SELECT email FROM leads WHERE email LIKE '%tataconsumer%'").fetchall(),
)
print(
    "job:",
    c.execute(
        "SELECT id, status, harvest_pid, listing_pid FROM search_jobs WHERE id=2"
    ).fetchone(),
)
