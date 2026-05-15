from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):

    DATABASE_URL: str

    EVENTS_WEBHOOK_URL: str = ""

    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET: str
    MINIO_SECURE: bool = False

    # File Upload
    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB
    ALLOWED_FILE_EXTENSIONS: list = [
        "pdf", "doc", "docx", "xls", "xlsx", "txt",
        "jpg", "jpeg", "png", "gif", "pptx",
    ]

    IMAP_HOST: str = ""
    IMAP_USER: str = ""
    IMAP_PASSWORD: str = ""
    IMAP_FOLDER: str = ""
    IMAP_IDLE_TIMEOUT: int = 100
    # App
    APP_ENV: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False

    # LLM
    GPT_URL: str

    # API
    API_TITLE: str = "Corporate Announcements API"
    API_VERSION: str = "1.0.0"

    #WEAVIATE
    WEAVIATE_HOST: str
    WEAVIATE_PORT: str
    WEAVIATE_GRPS: str


    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
