"""
Configuration module for the announcements system.
Loads environment variables and provides app settings.
"""
 
from pydantic_settings import BaseSettings
from typing import Literal
 
 
class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
   
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://pure_rag:VuEB0XDqHro2WDS@10.0.99.70:5432/sme_db"
   
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
   
    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "announcements"
    MINIO_USE_SSL: bool = False
    MINIO_SECURE: bool = False  # http (без TLS)
   
    # File Upload
    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB
    ALLOWED_FILE_EXTENSIONS: list = ["pdf", "doc", "docx", "xls", "xlsx", "txt", "jpg", "jpeg", "png", "gif", "pptx"]
   
    # App Settings
    APP_ENV: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "your-secret-key-change-in-production"
   
    # API Settings
    API_TITLE: str = "Corporate Announcements API"
    API_VERSION: str = "1.0.0"
   
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"
 
 
settings = Settings()