import os
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from models.base import Base
import models  # noqa: F401  (register all mappers)

TEST_DB_URL = os.environ["DATABASE_URL"]


# Function-scoped: asyncpg connections bind to the loop they were created on, and
# session-scope here raises "attached to a different loop" under pytest-asyncio auto mode.
@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, future=True)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS cchub_announcements"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    """Function-scoped session wrapped in a transaction that is rolled back."""
    connection = await engine.connect()
    trans = await connection.begin()
    maker = async_sessionmaker(bind=connection, expire_on_commit=False, class_=AsyncSession)
    sess = maker()
    try:
        yield sess
    finally:
        await sess.close()
        await trans.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def client(session):
    import httpx
    from fastapi import FastAPI
    from routers import router
    from dependencies import get_db

    app = FastAPI()
    app.include_router(router)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
