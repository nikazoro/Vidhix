from collections.abc import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from config import get_settings


log = structlog.get_logger(__name__)
settings = get_settings()

async_engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("Database session rollback due to exception")
            raise
        finally:
            await session.close()
            
async def init_db() -> None:
    # Import all models so Base.metadata is populated before create_all
    from models import (  # noqa: F401
        audit_log,
        case,
        case_step,
        corpus_document,
        document,
        message,
        response_feedback,
        session,
        workflow_state,
    )

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    log.info("database_initialized", tables=list(Base.metadata.tables.keys()))