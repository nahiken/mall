"""SQLAlchemy async engine and session factory for MySQL."""
import logging
from urllib.parse import quote_plus
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from .config import DB_CONFIG, DB_POOL_MAX_OVERFLOW, DB_POOL_SIZE

logger = logging.getLogger("mall_agent.db")

_engine = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    """Build mysql+aiomysql URL from config."""
    cfg = DB_CONFIG
    return (
        f"mysql+aiomysql://{cfg['user']}:{quote_plus(cfg['password'])}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['database']}"
        f"?charset=utf8mb4"
    )


async def init_db():
    """Create engine, session factory, and tables."""
    global _engine, _async_session_factory
    url = get_database_url()
    logger.info("Connecting to MySQL: %s@%s:%s/%s",
                DB_CONFIG["user"], DB_CONFIG["host"], DB_CONFIG["port"], DB_CONFIG["database"])
    _engine = create_async_engine(
        url,
        echo=False,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_POOL_MAX_OVERFLOW,
    )
    _async_session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    # Create tables
    from . import models  # noqa: ensure models are imported
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")


def get_session() -> async_sessionmaker[AsyncSession]:
    """Get the async session factory. Usage: async with get_session() as session:"""
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _async_session_factory


async def close_db():
    """Dispose the engine."""
    global _engine
    if _engine:
        await _engine.dispose()
        logger.info("Database connection closed")
