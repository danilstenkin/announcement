"""
Database setup with SQLAlchemy async support.
Initializes the async engine, session, and base model for all database models.
"""
 
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings
 
# Create async database engine
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True,  # Test connections before using them
    connect_args={"timeout": 10},
)
 
# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)
 
# Base class for all models
Base = declarative_base()
 
 
async def get_db():
    """Async dependency to get database session."""
    async with AsyncSessionLocal() as session:
        yield session