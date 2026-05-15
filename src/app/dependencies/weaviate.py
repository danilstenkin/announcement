from __future__ import annotations
import http

import weaviate
from typing import List
from config import settings
from logger import get_logger

logger = get_logger(__name__)


class WeaviateClient:
    def __init__(self):
        self.host = settings.WEAVIATE_HOST
        self.http_port = settings.WEAVIATE_PORT
        self.grpc_port = settings.WEAVIATE_GRPS

        self.collection_name = "ForteGuide"

    def _get_client(self):
        return weaviate.use_async_with_custom(
            http_host = self.host,
            http_port = self.http_port,
            http_grps = self.grpc_port,
            http_secure = False,
            grps_host = self.host,
            grps_port = self.grpc_port,
            grps_secure = False,
            skip_init_checks=True,
        )
