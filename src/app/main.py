"""
Main FastAPI application.
Initializes the app, configures routes, and middleware.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from prometheus_fastapi_instrumentator import Instrumentator

from config import settings
from dependencies.database import engine
from dependencies.minio import init_minio
from dependencies.gpt import GPTConfig, GPTClient
from workers.outlook_worker import run_email_watcher
from logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting API... ENV={settings.APP_ENV}")
    logger.info("Database tables managed by Alembic migrations")

    watcher_task = asyncio.create_task(run_email_watcher())
    logger.info("Email watcher started")

    init_minio()
    logger.info(f"MinIO connected: {settings.MINIO_ENDPOINT}")

    app.state.gpt = GPTClient(GPTConfig())
    logger.info(f"GPT client initialized")

    yield

    watcher_task.cancel()
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass
    logger.info("Email watcher stoped")


    await engine.dispose()
    logger.info("Shutting down API...")


app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    root_path="/announce",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    description="Corporate Announcements System API",
    lifespan=lifespan,
)


# ==================== Middleware ====================

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-User-Id",
        "X-User-Name",
        "X-User-Email",
        "Accept",
    ],
)


# ==================== Routes ====================
from routers import router  # noqa: E402
app.include_router(router)




# ==================== Health Check ====================
@app.get(
    "/health",
    tags=["health"],
    summary="Health check",
    description="Check if API is running",
)
async def health_check():

    gpt = await app.state.gpt.format_email(
        email_body="Привет",
        template="ответь",
        prompt="привет"

    )

    print(gpt)

    # helth()
    return {
        "status": "healthy",
        "version": settings.API_VERSION,
        "environment": settings.APP_ENV,
    }


# ==================== Root ====================
@app.get(
    "/",
    tags=["root"],
    summary="API information",
    description="Get API information",
)
async def root():

    return {
        "name": settings.API_TITLE,
        "version": settings.API_VERSION,
        "description": "Corporate Announcements System API",
        "docs": "/docs",
        "redoc": "/redoc",
    }


# ==================== Exception Handlers ====================
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8004,
        reload=settings.DEBUG,
        log_level="info",

    )
