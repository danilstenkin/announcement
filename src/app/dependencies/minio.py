from minio import Minio
from minio.error import S3Error
from minio.commonconfig import CopySource

import io
import os
from datetime import datetime, timezone

from config import settings
from logger import get_logger

logger = get_logger(__name__)


class MinIOClient:
    """MinIO client for handling file uploads."""

    def __init__(self):
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self.bucket_name = settings.MINIO_BUCKET
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
                logger.info(f"Created MinIO bucket: {self.bucket_name}")
            else:
                logger.info(f"MinIO bucket exists: {self.bucket_name}")
        except S3Error as e:
            logger.error(f"Error checking/creating MinIO bucket: {e}")
            raise

    async def upload_file(
        self,
        file_content: bytes,
        filename: str,
        announcement_id: str,
    ) -> str:
        """Upload a file to MinIO and return the object key."""
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_filename = os.path.basename(filename)
            object_key = f"announcements/{announcement_id}/{timestamp}_{safe_filename}"


            file_size = len(file_content)
            self.client.put_object(
                bucket_name=self.bucket_name,
                object_name=object_key,
                data=io.BytesIO(file_content),
                length=file_size,
            )

            logger.info(f"Uploaded file to MinIO: {object_key} (size: {file_size} bytes)")
            return object_key

        except S3Error as e:
            logger.error(f"Failed to upload file to MinIO: {e}")
            raise

    async def download_file(self, object_key: str) -> bytes:
        """Read an object's bytes from MinIO."""
        response = None
        try:
            response = self.client.get_object(
                bucket_name=self.bucket_name,
                object_name=object_key,
            )
            return response.read()
        except S3Error as e:
            logger.error(f"Failed to download file from MinIO: {e}")
            raise
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    async def delete_file(self, object_key: str) -> None:
        try:
            self.client.remove_object(
                bucket_name=self.bucket_name,
                object_name=object_key,
            )
            logger.info(f"Deleted file from MinIO: {object_key}")
        except S3Error as e:
            logger.error(f"Failed to delete file from MinIO: {e}")
            raise

    async def copy_file(self, source_key: str, dest_key: str) -> str:
        try:
            self.client.copy_object(
                bucket_name=self.bucket_name,
                object_name=dest_key,
                source=CopySource(self.bucket_name, source_key),
            )
            return dest_key
        except S3Error as e:
            logger.error(f"Failed to copy MinIO object: {e}")
            raise

    def get_file_url(self, object_key: str, expiry_seconds: int = 3600) -> str:
        from datetime import timedelta
        try:
            url = self.client.presigned_get_object(
                bucket_name=self.bucket_name,
                object_name=object_key,
                expires=timedelta(seconds=expiry_seconds),
            )
            return url
        except S3Error as e:
            logger.error(f"Failed to get file URL from MinIO: {e}")
            raise


_minio_client: MinIOClient | None = None


def init_minio() -> MinIOClient:
    """Initialize MinIO client on application startup."""
    global _minio_client
    if _minio_client is None:
        _minio_client = MinIOClient()
    return _minio_client


def get_minio_client() -> MinIOClient:
    """Get MinIO client instance (singleton)."""
    global _minio_client
    if _minio_client is None:
        _minio_client = MinIOClient()
    return _minio_client


def get_minio() -> MinIOClient:
    return get_minio_client()
