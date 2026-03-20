"""
MinIO client for file storage.
Handles file uploads to MinIO S3-compatible object storage.
"""
 
from minio import Minio
from minio.error import S3Error
from config import settings
from logger import get_logger
import io
from datetime import datetime
import os
 
logger = get_logger(__name__)
 
 
class MinIOClient:
    """MinIO client for handling file uploads."""
   
    def __init__(self):
        """Initialize MinIO client."""
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self.bucket_name = settings.MINIO_BUCKET
        self._ensure_bucket_exists()
   
    def _ensure_bucket_exists(self):
        """Ensure the bucket exists, create if not."""
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
        announcement_id: str
    ) -> str:
        """
        Upload a file to MinIO and return the object key.
       
        Args:
            file_content: File content as bytes
            filename: Original filename
            announcement_id: Announcement ID for organizing files
           
        Returns:
            Object key (path) in MinIO
           
        Raises:
            S3Error: If upload fails
        """
        try:
            # Generate object key: announcements/{announcement_id}/{timestamp}_{filename}
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            # Keep only filename, remove directory parts
            safe_filename = os.path.basename(filename)
            object_key = f"announcements/{announcement_id}/{timestamp}_{safe_filename}"
           
            # Upload file
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
   
    async def delete_file(self, object_key: str) -> None:
        """
        Delete a file from MinIO.
       
        Args:
            object_key: Object key (path) to delete
           
        Raises:
            S3Error: If deletion fails
        """
        try:
            self.client.remove_object(
                bucket_name=self.bucket_name,
                object_name=object_key,
            )
            logger.info(f"Deleted file from MinIO: {object_key}")
        except S3Error as e:
            logger.error(f"Failed to delete file from MinIO: {e}")
            raise
   
    def get_file_url(self, object_key: str, expiry_seconds: int = 3600) -> str:
        """
        Get a presigned URL for accessing a file.
       
        Args:
            object_key: Object key (path)
            expiry_seconds: URL expiration time in seconds (default 1 hour)
           
        Returns:
            Presigned URL
        """
        try:
            url = self.client.get_presigned_download_link(
                bucket_name=self.bucket_name,
                object_name=object_key,
                expires=expiry_seconds,
            )
            return url
        except S3Error as e:
            logger.error(f"Failed to get file URL from MinIO: {e}")
            raise
 
 
# Singleton instance
_minio_client = None
 
 
def get_minio_client() -> MinIOClient:
    """Get MinIO client instance."""
    global _minio_client
    if _minio_client is None:
        _minio_client = MinIOClient()
    return _minio_client