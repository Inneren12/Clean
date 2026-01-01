import asyncio
import hashlib
import hmac
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qsl, urlparse

import boto3
from botocore.client import Config

from app.settings import settings
from app.shared.circuit_breaker import CircuitBreaker


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

    async def upload(
        self, *, key: str, body: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        """Alias for put() for backward compatibility."""
        return await self.put(key=key, body=body, content_type=content_type)

    @abstractmethod
    async def read(self, *, key: str) -> bytes:
        """Return the object payload as bytes."""

    async def read_bytes(self, *, key: str) -> bytes:
        """Alias for read() for backward compatibility."""
        return await self.read(key=key)

    async def download(self, *, key: str) -> bytes:
        """Alias for read() for backward compatibility."""
        return await self.read(key=key)

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

    def validate_signed_get_url(self, *, key: str, url: str) -> bool:
        """Validate a signed URL for direct IO backends."""

        return False

    def supports_direct_io(self) -> bool:
        """Whether the backend can be read directly without a signed URL."""

        return False


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: Path, signing_secret: str | None = None) -> None:
        self.root = root
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

    def validate_signed_get_url(self, *, key: str, url: str) -> bool:
        parsed = urlparse(url)
        params = dict(parse_qsl(parsed.query))
        sig = params.get("sig")
        exp_raw = params.get("exp")
        if not sig or not exp_raw:
            return False
        try:
            expires_at = int(exp_raw)
        except ValueError:
            return False
        return self.validate_signature(key=key, expires_at=expires_at, signature=sig)

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


class S3StorageBackend(StorageBackend):
    def __init__(
        self,
        *,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str | None = None,
        endpoint: str | None = None,
        connect_timeout: float = 3.0,
        read_timeout: float = 10.0,
        max_attempts: int = 4,
        max_payload_bytes: int | None = None,
        enable_circuit_breaker: bool = True,
        circuit_failure_threshold: int | None = None,
        circuit_recovery_seconds: float | None = None,
        circuit_window_seconds: float | None = None,
        client: Any | None = None,
    ) -> None:
        if client:
            self.client = client
        else:
            session = boto3.session.Session()
            self.client = session.client(
                "s3",
                endpoint_url=endpoint,
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                    retries={"mode": "standard", "max_attempts": max(1, max_attempts)},
                ),
            )
        self.bucket = bucket
        self.max_payload_bytes = max_payload_bytes
        self._breaker: CircuitBreaker | None = None
        if enable_circuit_breaker:
            self._breaker = CircuitBreaker(
                name="s3",
                failure_threshold=circuit_failure_threshold or settings.s3_circuit_failure_threshold,
                recovery_time=circuit_recovery_seconds or settings.s3_circuit_recovery_seconds,
                window_seconds=circuit_window_seconds or settings.s3_circuit_window_seconds,
            )

    async def put(
        self, *, key: str, body: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        buffer = bytearray()
        async for chunk in body:
            buffer.extend(chunk)
            if self.max_payload_bytes and len(buffer) > self.max_payload_bytes:
                raise ValueError("Payload exceeds configured upload limit")
        data = bytes(buffer)

        def _upload() -> None:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        await self._run_with_circuit(lambda: asyncio.to_thread(_upload))
        return StoredObject(key=key, size=len(data), content_type=content_type)

    async def read(self, *, key: str) -> bytes:
        def _download() -> bytes:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()

        return await self._run_with_circuit(lambda: asyncio.to_thread(_download))

    async def delete(self, *, key: str) -> None:
        def _delete() -> None:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        await self._run_with_circuit(lambda: asyncio.to_thread(_delete))

    async def list(self, *, prefix: str = "") -> list[str]:
        keys: list[str] = []

        def _list() -> None:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    keys.append(item["Key"])
        await self._run_with_circuit(lambda: asyncio.to_thread(_list))
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

        return await self._run_with_circuit(lambda: asyncio.to_thread(_sign))

    async def _run_with_circuit(self, fn):
        if self._breaker is None:
            result = fn()
            if asyncio.iscoroutine(result):
                return await result
            return result
        return await self._breaker.call(fn)


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
