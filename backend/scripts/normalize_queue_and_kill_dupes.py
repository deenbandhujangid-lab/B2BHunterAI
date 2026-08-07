"""Normalize company_queue status to lowercase values + kill duplicate workers."""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "local_database.db"


def kill_dupes() -> None:
    ps = r"""
Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -match 'harvest_worker|load_worker' -and
    $_.CommandLine -match 'pythoncore|Local\\Python'
  } |
  ForEach-Object {
    Write-Host ('KILL_SYSTEM ' + $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)


def main() -> None:
    kill_dupes()
    con = sqlite3.connect(str(DB), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    cur = con.cursor()
    mapping = [
        ("PENDING", "pending"),
        ("HARVESTING", "harvesting"),
        ("DONE", "done"),
        ("FAILED", "failed"),
    ]
    for old, new in mapping:
        cur.execute(
            "UPDATE company_queue SET status=? WHERE status=?",
            (new, old),
        )
        print(f"{old} -> {new}: {cur.rowcount}")
    cur.execute(
        "UPDATE search_jobs SET harvest_pid=NULL WHERE id=2"
    )
    con.commit()
    print("statuses:", cur.execute(
        "SELECT status, COUNT(1) FROM company_queue GROUP BY status"
    ).fetchall())
    print(
        "pending sample freshworks:",
        cur.execute(
            "SELECT status FROM company_queue WHERE domain='freshworks.com'"
        ).fetchone(),
    )
    con.close()
    print("done")


if __name__ == "__main__":
    main()
