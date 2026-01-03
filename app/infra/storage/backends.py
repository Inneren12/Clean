import asyncio
import hashlib
import hmac
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qsl, urlparse

import boto3
from botocore.client import Config
import httpx
from fastapi import HTTPException, status

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
        self,
        *,
        key: str,
        expires_in: int,
        resource_url: str | None = None,
        variant: str | None = None,
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

    def _fallback_keys(self, cleaned: str) -> list[str]:
        fallbacks: list[str] = []
        parts = cleaned.split("/")

        if parts and parts[0] == "orders":
            without_prefix = "/".join(parts[1:])
            if without_prefix:
                fallbacks.append(without_prefix)
            if len(parts) >= 4:
                org_part, order_part = parts[1], parts[2]
                remaining = "/".join(parts[3:])
                fallbacks.append(f"org/{org_part}/bookings/{order_part}/{remaining}")
            if len(parts) >= 3:
                fallbacks.append("orders/" + "/".join(parts[2:]))
        elif parts and parts[0] == "org" and len(parts) >= 4 and parts[2] == "bookings":
            org_part, order_part = parts[1], parts[3]
            remaining = "/".join(parts[4:])
            fallbacks.append(f"orders/{org_part}/{order_part}/{remaining}")
        elif parts and parts[0] == "bookings" and len(parts) >= 2:
            order_part = parts[1]
            remaining = "/".join(parts[2:])
            fallbacks.append(f"orders/{order_part}/{remaining}")

        return [f for f in fallbacks if f]

    def _canonical_key(self, cleaned: str) -> str:
        return cleaned

    def _resolve(self, key: str, *, create_parents: bool) -> Path:
        cleaned = key.lstrip("/")
        canonical_key = self._canonical_key(cleaned)
        root_resolved = self.root.resolve()
        primary = (self.root / canonical_key).resolve()

        if not str(primary).startswith(str(root_resolved)):
            raise ValueError("Invalid storage key")

        if create_parents:
            primary.parent.mkdir(parents=True, exist_ok=True)
            return primary

        if primary.exists():
            return primary

        if canonical_key != cleaned:
            legacy = (self.root / cleaned).resolve()
            if str(legacy).startswith(str(root_resolved)) and legacy.exists():
                return legacy

        for fallback in self._fallback_keys(cleaned):
            candidate = (self.root / fallback).resolve()
            if not str(candidate).startswith(str(root_resolved)):
                continue
            if candidate.exists():
                return candidate

        return primary

    def _write_aliases(self, cleaned: str) -> list[str]:
        parts = cleaned.split("/")
        if parts and parts[0] == "orders" and len(parts) >= 3:
            org_part, order_part = parts[1], parts[2]
            remaining = "/".join(parts[3:])
            suffix = f"/{remaining}" if remaining else ""
            return [f"org/{org_part}/bookings/{order_part}{suffix}"]
        return []

    async def put(
        self, *, key: str, body: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        cleaned = key.lstrip("/")
        path = self._resolve(cleaned, create_parents=True)
        size = 0

        async def _write() -> None:
            nonlocal size
            with path.open("wb") as f:
                async for chunk in body:
                    size += len(chunk)
                    f.write(chunk)

        await _write()

        for alias in self._write_aliases(cleaned):
            alias_path = self._resolve(alias, create_parents=True)
            await asyncio.to_thread(shutil.copyfile, path, alias_path)

        return StoredObject(key=key, size=size, content_type=content_type)

    async def read(self, *, key: str) -> bytes:
        path = self._resolve(key, create_parents=False)

        def _read() -> bytes:
            return path.read_bytes()

        return await asyncio.to_thread(_read)

    async def delete(self, *, key: str) -> None:
        path = self._resolve(key, create_parents=False)
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
        self,
        *,
        key: str,
        expires_in: int,
        resource_url: str | None = None,
        variant: str | None = None,
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
        return self._resolve(key, create_parents=False)


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
        public_base_url: str | None = None,
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
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None
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
        self,
        *,
        key: str,
        expires_in: int,
        resource_url: str | None = None,
        variant: str | None = None,
    ) -> str:
        if self.public_base_url:
            return f"{self.public_base_url}/{key.lstrip('/')}"

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
        self,
        *,
        key: str,
        expires_in: int,
        resource_url: str | None = None,
        variant: str | None = None,
    ) -> str:
        expires_at = int(time.time()) + expires_in
        return resource_url or f"https://example.invalid/{key}?exp={expires_at}"


class CloudflareImagesStorageBackend(StorageBackend):
    def __init__(
        self,
        *,
        account_id: str,
        api_token: str,
        account_hash: str,
        default_variant: str,
        signing_key: str | None = None,
        max_payload_bytes: int | None = None,
        api_base_url: str = "https://api.cloudflare.com",
        delivery_base_url: str = "https://imagedelivery.net",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.account_id = account_id
        self.account_hash = account_hash
        self.default_variant = default_variant
        self.signing_key = signing_key
        self.max_payload_bytes = max_payload_bytes
        self.delivery_base_url = delivery_base_url.rstrip("/")
        self.api_base_url = api_base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(
            base_url=self.api_base_url,
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def put(
        self, *, key: str, body: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        buffer = bytearray()
        async for chunk in body:
            buffer.extend(chunk)
            if self.max_payload_bytes and len(buffer) > self.max_payload_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="File too large",
                )
        payload = bytes(buffer)

        try:
            response = await self.client.post(
                f"/client/v4/accounts/{self.account_id}/images/v1",
                data={"requireSignedURLs": "true"},
                files={"file": (Path(key).name or "upload", payload, content_type)},
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cloudflare Images upload failed",
            ) from exc

        data: dict[str, Any]
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            data = {}

        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cloudflare Images unavailable",
            )

        if not data.get("success"):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Cloudflare Images upload failed",
            )

        image_id = (data.get("result") or {}).get("id")
        if not image_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Cloudflare Images upload failed",
            )

        return StoredObject(key=str(image_id), size=len(payload), content_type=content_type)

    async def read(self, *, key: str) -> bytes:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Direct reads are not supported for Cloudflare Images",
        )

    async def delete(self, *, key: str) -> None:
        try:
            response = await self.client.delete(
                f"/client/v4/accounts/{self.account_id}/images/v1/{key}"
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cloudflare Images delete failed",
            ) from exc

        if response.status_code == 404:
            return

        data: dict[str, Any]
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            data = {}

        if response.status_code >= 500 or not data.get("success", True):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cloudflare Images delete failed",
            )

    async def list(self, *, prefix: str = "") -> list[str]:  # pragma: no cover - unused
        return []

    def _delivery_url(self, key: str, variant: str | None = None) -> str:
        effective_variant = (variant or self.default_variant).strip("/") or self.default_variant
        return f"{self.delivery_base_url}/{self.account_hash}/{key}/{effective_variant}"

    async def generate_signed_get_url(
        self,
        *,
        key: str,
        expires_in: int,
        resource_url: str | None = None,
        variant: str | None = None,
    ) -> str:
        if not self.signing_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cloudflare Images signing key not configured",
            )

        base = self._delivery_url(key, variant)
        exp = int(time.time()) + expires_in
        parsed = urlparse(base)
        query = f"exp={exp}"
        to_sign = f"{parsed.path}?{query}"
        sig = hmac.new(self.signing_key.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
        final_query = f"{query}&sig={sig}"
        return parsed._replace(query=final_query).geturl()

    def supports_direct_io(self) -> bool:
        return False
