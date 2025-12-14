from __future__ import annotations

import os
from typing import Dict, Optional

import boto3


class ImageStore:
    def __init__(self) -> None:
        self._mem: Dict[str, bytes] = {}

        self._bucket = os.getenv("R2_BUCKET")
        self._endpoint = os.getenv("R2_ENDPOINT")
        self._access_key = os.getenv("R2_ACCESS_KEY_ID")
        self._secret_key = os.getenv("R2_SECRET_ACCESS_KEY")

        self._s3 = None
        if self._bucket and self._endpoint and self._access_key and self._secret_key:
            self._s3 = boto3.client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name="auto",
            )

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        # Always keep a local in-memory copy for local runs / debugging.
        self._mem[key] = data

        if not self._s3 or not self._bucket:
            return

        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )

    def get(self, key: str) -> Optional[bytes]:
        if key in self._mem:
            return self._mem[key]

        if not self._s3 or not self._bucket:
            return None

        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=key)
            body = obj.get("Body")
            if body is None:
                return None
            data = body.read()
            # Cache in memory for subsequent requests.
            self._mem[key] = data
            return data
        except Exception:
            return None
