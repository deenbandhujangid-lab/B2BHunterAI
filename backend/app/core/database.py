from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # never flood console — blocks / hangs under load on Windows
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_on_connect(dbapi_conn, connection_record):
    """WAL so DB Browser can refresh while scraper writes."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def _ensure_columns(conn):
    """SQLite: add missing columns / unique email index on existing DBs."""
    result = await conn.execute(text("PRAGMA table_info(search_jobs)"))
    cols = {row[1] for row in result.fetchall()}
    if "restart_count" not in cols:
        await conn.execute(
            text("ALTER TABLE search_jobs ADD COLUMN restart_count INTEGER DEFAULT 0")
        )
    if "last_error" not in cols:
        await conn.execute(
            text("ALTER TABLE search_jobs ADD COLUMN last_error TEXT")
        )
    if "activity_message" not in cols:
        await conn.execute(
            text("ALTER TABLE search_jobs ADD COLUMN activity_message TEXT")
        )
    if "last_activity_at" not in cols:
        await conn.execute(
            text("ALTER TABLE search_jobs ADD COLUMN last_activity_at DATETIME")
        )
    if "scrape_checkpoint" not in cols:
        await conn.execute(
            text("ALTER TABLE search_jobs ADD COLUMN scrape_checkpoint TEXT")
        )
    if "harvest_pid" not in cols:
        await conn.execute(
            text("ALTER TABLE search_jobs ADD COLUMN harvest_pid INTEGER")
        )
    if "listing_pid" not in cols:
        await conn.execute(
            text("ALTER TABLE search_jobs ADD COLUMN listing_pid INTEGER")
        )

    # company_queue enrichment columns
    try:
        cq = await conn.execute(text("PRAGMA table_info(company_queue)"))
        cq_cols = {row[1] for row in cq.fetchall()}
        if cq_cols:
            if "website" not in cq_cols:
                await conn.execute(
                    text("ALTER TABLE company_queue ADD COLUMN website VARCHAR(500)")
                )
            if "source_query" not in cq_cols:
                await conn.execute(
                    text("ALTER TABLE company_queue ADD COLUMN source_query VARCHAR(500)")
                )
            if "source_url" not in cq_cols:
                await conn.execute(
                    text("ALTER TABLE company_queue ADD COLUMN source_url VARCHAR(1000)")
                )
            if "linkedin_url" not in cq_cols:
                await conn.execute(
                    text("ALTER TABLE company_queue ADD COLUMN linkedin_url VARCHAR(500)")
                )
            if "size_label" not in cq_cols:
                await conn.execute(
                    text("ALTER TABLE company_queue ADD COLUMN size_label VARCHAR(20)")
                )
    except Exception:
        pass

    # Deduplicate emails keeping lowest id, then enforce unique index
    try:
        await conn.execute(text("""
            DELETE FROM leads
            WHERE id NOT IN (
                SELECT MIN(id) FROM leads GROUP BY lower(email)
            )
        """))
        await conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ux_leads_email ON leads(email)")
        )
    except Exception:
        pass


async def init_db():
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await _ensure_columns(conn)
        except Exception:
            pass
