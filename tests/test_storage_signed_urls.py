"""
Tests for storage backend signed URL generation and validation.

These tests verify that signed URLs are properly generated and validated
for the LocalStorageBackend.
"""

import asyncio
import time
from pathlib import Path

import pytest

from app.infra.storage.backends import LocalStorageBackend, InMemoryStorageBackend


class TestLocalStorageBackendSignedUrls:
    """Tests for LocalStorageBackend signed URL functionality."""

    def test_generate_and_validate_signed_url(self, tmp_path):
        """
        Test that a generated signed URL can be validated successfully.
        """
        backend = LocalStorageBackend(
            root=tmp_path,
            signing_secret="test-secret-key-for-hmac-signing"
        )

        key = "test-file.txt"
        resource_url = "https://api.example.test/download"
        expires_in = 3600  # 1 hour

        # Generate signed URL
        signed_url = asyncio.run(
            backend.generate_signed_get_url(
                key=key,
                expires_in=expires_in,
                resource_url=resource_url
            )
        )

        # Verify URL was generated
        assert signed_url.startswith(resource_url)
        assert "exp=" in signed_url
        assert "sig=" in signed_url

        # Validate the signed URL
        is_valid = backend.validate_signed_get_url(key=key, url=signed_url)
        assert is_valid is True

    def test_signed_url_validation_fails_with_wrong_key(self, tmp_path):
        """
        Test that validation fails when key doesn't match.
        """
        backend = LocalStorageBackend(
            root=tmp_path,
            signing_secret="test-secret-key-for-hmac-signing"
        )

        key = "test-file.txt"
        wrong_key = "different-file.txt"
        resource_url = "https://api.example.test/download"
        expires_in = 3600

        # Generate signed URL for one key
        signed_url = asyncio.run(
            backend.generate_signed_get_url(
                key=key,
                expires_in=expires_in,
                resource_url=resource_url
            )
        )

        # Try to validate with different key
        is_valid = backend.validate_signed_get_url(key=wrong_key, url=signed_url)
        assert is_valid is False

    def test_signed_url_validation_fails_when_expired(self, tmp_path):
        """
        Test that validation fails for expired URLs.
        """
        backend = LocalStorageBackend(
            root=tmp_path,
            signing_secret="test-secret-key-for-hmac-signing"
        )

        key = "test-file.txt"
        resource_url = "https://api.example.test/download"
        expires_in = 1  # 1 second

        # Generate signed URL
        signed_url = asyncio.run(
            backend.generate_signed_get_url(
                key=key,
                expires_in=expires_in,
                resource_url=resource_url
            )
        )

        # Wait for expiration
        time.sleep(2)

        # Try to validate expired URL
        is_valid = backend.validate_signed_get_url(key=key, url=signed_url)
        assert is_valid is False

    def test_signed_url_validation_uses_constant_time_comparison(self, tmp_path):
        """
        Test that signature validation uses constant-time comparison.

        This is a security best practice to prevent timing attacks.
        """
        from app.infra.storage import backends
        import inspect

        # Get source code of validate_signature method (used by validate_signed_get_url)
        source = inspect.getsource(backends.LocalStorageBackend.validate_signature)

        # Verify constant-time comparison is used
        assert "hmac.compare_digest" in source or "compare_digest" in source, \
            "Signed URL validation should use constant-time comparison (hmac.compare_digest)"

    def test_local_backend_full_workflow(self, tmp_path):
        """
        Test full workflow: PUT file -> generate signed URL -> validate URL -> read file.
        """
        backend = LocalStorageBackend(
            root=tmp_path,
            signing_secret="test-secret-key-for-hmac-signing"
        )

        key = "uploads/photo.jpg"
        content = b"fake-image-data"

        async def workflow():
            # Put file
            async def content_iterator():
                yield content

            stored = await backend.put(
                key=key,
                body=content_iterator(),
                content_type="image/jpeg"
            )
            assert stored.key == key
            assert stored.size == len(content)

            # Generate signed URL
            signed_url = await backend.generate_signed_get_url(
                key=key,
                expires_in=3600,
                resource_url="https://api.example.test/download"
            )
            assert signed_url is not None

            # Validate signed URL
            is_valid = backend.validate_signed_get_url(key=key, url=signed_url)
            assert is_valid is True

            # Read file back
            read_content = await backend.read(key=key)
            assert read_content == content

        asyncio.run(workflow())


class TestInMemoryStorageBackendSignedUrls:
    """Tests for InMemoryStorageBackend signed URL functionality."""

    def test_generate_signed_url_with_resource_url(self):
        """
        Test that InMemoryStorageBackend generates URLs with provided resource_url.
        """
        backend = InMemoryStorageBackend()

        key = "test-file.txt"
        resource_url = "https://api.example.test/download"
        expires_in = 3600

        signed_url = asyncio.run(
            backend.generate_signed_get_url(
                key=key,
                expires_in=expires_in,
                resource_url=resource_url
            )
        )

        assert signed_url.startswith("https://example.invalid/test-file.txt")
        assert "exp=" in signed_url

    def test_in_memory_backend_full_workflow(self):
        """
        Test full workflow for in-memory backend.
        """
        backend = InMemoryStorageBackend()

        key = "uploads/photo.jpg"
        content = b"fake-image-data"

        async def workflow():
            # Put file
            async def content_iterator():
                yield content

            stored = await backend.put(
                key=key,
                body=content_iterator(),
                content_type="image/jpeg"
            )
            assert stored.key == key
            assert stored.size == len(content)

            signed_url = await backend.generate_signed_get_url(
                key=key,
                expires_in=3600,
                resource_url="https://api.example.test/download"
            )
            assert signed_url.startswith("https://example.invalid/uploads/photo.jpg")

            # Read file back
            read_content = await backend.read(key=key)
            assert read_content == content

        asyncio.run(workflow())
