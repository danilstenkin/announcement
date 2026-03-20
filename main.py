"""
Main FastAPI application.
Initializes the app, configures routes, and middleware.
"""
 
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import sys
import os
 
# Add app directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
 
from config import settings
from app.db import Base
from app.routers import announcements, sse
from redis_client import RedisClient
from logger import get_logger
 
logger = get_logger(__name__)
 
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifecycle.
    Runs on startup and shutdown.
    """
    # Startup
    logger.info("🚀 Starting announcements API...")
    logger.info(f"📊 Database: {settings.DATABASE_URL}")
    logger.info(f"📡 Redis: {settings.REDIS_URL}")
    logger.info("✅ Database tables managed by Alembic migrations")
   
    yield
   
    # Shutdown
    logger.info("🛑 Shutting down announcements API...")
    await RedisClient.close()
 
 
# Create FastAPI app
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
 
 
# ==================== CORS Middleware ====================
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://dl-ai-dev-nlp03-uv01.fortebank.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization", "X-User-Id", "X-User-Name", "X-User-Email", "Accept"],
)
 
 
# ==================== Routes ====================
 
app.include_router(announcements.router)
app.include_router(sse.router)
 
 
# ==================== Health Check ====================
 
@app.get(
    "/health",
    tags=["health"],
    summary="Health check",
    description="Check if API is running"
)
async def health_check():
    """
    Health check endpoint.
    Returns status of the API.
    """
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
    description="Get API information"
)
async def root():
    """Get API information."""
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
    """Handle HTTP exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
 
 
if __name__ == "__main__":
    import uvicorn
   
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info",
    )