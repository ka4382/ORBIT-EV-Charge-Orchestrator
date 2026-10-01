"""boto3 object store (MinIO in dev). boto3 is synchronous, so calls run in a worker thread."""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


class S3ObjectStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self._c = client
        self.bucket = bucket

    @classmethod
    def from_settings(
        cls, *, endpoint: str, access_key: str, secret_key: str, bucket: str, region: str = "us-east-1"
    ) -> S3ObjectStore:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )
        return cls(client, bucket)

    def ensure_bucket_sync(self) -> bool:
        """Create the bucket if it does not exist. Returns True when it was created."""
        try:
            self._c.head_bucket(Bucket=self.bucket)
            return False
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code not in ("404", "NoSuchBucket", "NotFound"):
                raise
        self._c.create_bucket(Bucket=self.bucket)
        return True

    async def ensure_bucket(self) -> bool:
        return await asyncio.to_thread(self.ensure_bucket_sync)

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(
            self._c.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType="application/octet-stream"
        )
