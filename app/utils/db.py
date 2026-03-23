"""Session de base de données async."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

# Détection SQLite vs PostgreSQL
is_sqlite = settings.database_url.startswith("sqlite")

# Async engine (pour FastAPI)
engine_kwargs = {
    "echo": settings.app_debug,
}
if not is_sqlite:
    engine_kwargs.update({
        "pool_size": 20,
        "max_overflow": 10,
        "pool_pre_ping": True,
    })

async_engine = create_async_engine(settings.database_url, **engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Sync engine (pour Celery workers et scripts)
sync_kwargs = {
    "echo": settings.app_debug,
}
if not is_sqlite:
    sync_kwargs.update({
        "pool_size": 10,
        "max_overflow": 5,
        "pool_pre_ping": True,
    })

sync_engine = create_engine(settings.database_url_sync, **sync_kwargs)

SyncSessionLocal = sessionmaker(bind=sync_engine)


async def get_db() -> AsyncSession:
    """Dépendance FastAPI pour obtenir une session DB."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
