import asyncio
import hashlib
import hmac
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

import boto3
from botocore.client import Config


@dataclass
class StoredObject:
    key: str
    size: int
    content_type: str


class StorageBackend(ABC):
    """Abstract interface for object storage."""

    @abstractmethod
    async def put(
        self, *, key: str, body: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        """Persist an object and return its metadata."""

    @abstractmethod
    async def read(self, *, key: str) -> bytes:
        """Return the object payload as bytes."""

    @abstractmethod
    async def delete(self, *, key: str) -> None:
        """Delete an object if it exists."""

    @abstractmethod
    async def list(self, *, prefix: str = "") -> list[str]:
        """List object keys under an optional prefix."""

    @abstractmethod
    async def generate_signed_get_url(
        self, *, key: str, expires_in: int, resource_url: str | None = None
    ) -> str:
        """Generate a signed URL to fetch an object."""

    def supports_direct_io(self) -> bool:
        """Whether the backend can be read directly without a signed URL."""

        return False


class LocalStorageBackend(StorageBackend):
    def __init__(
        self,
        root: Path | str | None = None,
        signing_secret: str | None = None,
        base_dir: Path | str | None = None,  # Backward compat alias for root
    ) -> None:
        # Support both root and base_dir parameters (base_dir is alias for root)
        if root is not None and base_dir is not None:
            raise ValueError("Cannot specify both 'root' and 'base_dir' parameters")

        if base_dir is not None:
            # Use base_dir if provided (backward compat)
            self.root = Path(base_dir) if isinstance(base_dir, str) else base_dir
        elif root is not None:
            self.root = Path(root) if isinstance(root, str) else root
        else:
            raise ValueError("Either 'root' or 'base_dir' parameter must be provided")

        self.signing_secret = signing_secret or "local-storage-secret"

    def _resolve(self, key: str) -> Path:
        cleaned = key.lstrip("/")
        candidate = (self.root / cleaned).resolve()
        root_resolved = self.root.resolve()
        if not str(candidate).startswith(str(root_resolved)):
            raise ValueError("Invalid storage key")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    async def put(
        self, *, key: str, body: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        path = self._resolve(key)
        size = 0

        async def _write() -> None:
            nonlocal size
            with path.open("wb") as f:
                async for chunk in body:
                    size += len(chunk)
                    f.write(chunk)

        await _write()
        return StoredObject(key=key, size=size, content_type=content_type)

    async def read(self, *, key: str) -> bytes:
        path = self._resolve(key)

        def _read() -> bytes:
            return path.read_bytes()

        return await asyncio.to_thread(_read)

    async def delete(self, *, key: str) -> None:
        path = self._resolve(key)
        path.unlink(missing_ok=True)

    async def list(self, *, prefix: str = "") -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        keys: list[str] = []
        for file in base.rglob("*"):
            if file.is_file():
                keys.append(str(file.relative_to(self.root).as_posix()))
        return keys

    async def generate_signed_get_url(
        self, *, key: str, expires_in: int, resource_url: str | None = None
    ) -> str:
        if not resource_url:
            raise ValueError("resource_url required for local storage signed URLs")
        expires_at = int(time.time()) + expires_in
        payload = f"{key}:{expires_at}".encode()
        signature = hmac.new(self.signing_secret.encode(), payload, hashlib.sha256).hexdigest()
        separator = "&" if "?" in resource_url else "?"
        return f"{resource_url}{separator}exp={expires_at}&sig={signature}"

    def supports_direct_io(self) -> bool:
        return True

    def validate_signature(self, *, key: str, expires_at: int, signature: str) -> bool:
        expected = hmac.new(
            self.signing_secret.encode(), f"{key}:{expires_at}".encode(), hashlib.sha256
        ).hexdigest()
        if expires_at < int(time.time()):
            return False
        return hmac.compare_digest(expected, signature)

    def path_for(self, key: str) -> Path:
        return self._resolve(key)

    # Convenience methods for simpler API (matching common usage patterns)
    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        """Upload bytes to storage (convenience wrapper for put)."""
        async def _data_stream():
            yield data

        return await self.put(key=key, body=_data_stream(), content_type=content_type)

    async def download(self, key: str) -> bytes:
        """Download bytes from storage (alias for read)."""
        return await self.read(key=key)

    async def exists(self, key: str) -> bool:
        """Check if a key exists in storage."""
        path = self._resolve(key)
        return await asyncio.to_thread(path.exists)


class S3StorageBackend(StorageBackend):
    def __init__(
        self,
        *,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        session = boto3.session.Session()
        self.client = session.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
        )
        self.bucket = bucket

    async def put(
        self, *, key: str, body: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        buffer = bytearray()
        async for chunk in body:
            buffer.extend(chunk)
        data = bytes(buffer)

        def _upload() -> None:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

        await asyncio.to_thread(_upload)
        return StoredObject(key=key, size=len(data), content_type=content_type)

    async def read(self, *, key: str) -> bytes:
        def _download() -> bytes:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()

        return await asyncio.to_thread(_download)

    async def delete(self, *, key: str) -> None:
        def _delete() -> None:
            self.client.delete_object(Bucket=self.bucket, Key=key)

        await asyncio.to_thread(_delete)

    async def list(self, *, prefix: str = "") -> list[str]:
        keys: list[str] = []

        def _list() -> None:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    keys.append(item["Key"])

        await asyncio.to_thread(_list)
        return keys

    async def generate_signed_get_url(
        self, *, key: str, expires_in: int, resource_url: str | None = None
    ) -> str:
        def _sign() -> str:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )

        return await asyncio.to_thread(_sign)


class InMemoryStorageBackend(StorageBackend):
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    async def put(
        self, *, key: str, body: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        data = bytearray()
        async for chunk in body:
            data.extend(chunk)
        payload = bytes(data)
        self._objects[key] = (payload, content_type)
        return StoredObject(key=key, size=len(payload), content_type=content_type)

    async def read(self, *, key: str) -> bytes:
        payload, _ = self._objects[key]
        return payload

    async def delete(self, *, key: str) -> None:
        self._objects.pop(key, None)

    async def list(self, *, prefix: str = "") -> list[str]:
        return [k for k in self._objects if k.startswith(prefix)]

    async def generate_signed_get_url(
        self, *, key: str, expires_in: int, resource_url: str | None = None
    ) -> str:
        expires_at = int(time.time()) + expires_in
        return resource_url or f"https://example.invalid/{key}?exp={expires_at}"
