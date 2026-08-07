"""
Windows-safe uvicorn entrypoint.
Playwright needs ProactorEventLoop — do NOT use --reload on Windows.
"""
from __future__ import annotations

import asyncio
import sys

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8002,
        reload=False,
        loop="asyncio",
    )
