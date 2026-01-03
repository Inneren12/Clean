import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app.api import photo_tokens
from app.api.photo_tokens import build_photo_download_token, verify_photo_download_token
from app.settings import settings


def test_verify_returns_expected_claims(monkeypatch):
    monkeypatch.setattr(settings, "photo_token_bind_ua", True)
    token = build_photo_download_token(
        org_id=settings.default_org_id,
        order_id="order-1",
        photo_id="photo-1",
        user_agent="agent",
    )

    claims = asyncio.run(verify_photo_download_token(token, user_agent="agent"))

    assert claims.org_id == settings.default_org_id
    assert claims.order_id == "order-1"
    assert claims.photo_id == "photo-1"


def test_ua_mismatch_rejected(monkeypatch):
    monkeypatch.setattr(settings, "photo_token_bind_ua", True)
    token = build_photo_download_token(
        org_id=settings.default_org_id,
        order_id="order-1",
        photo_id="photo-1",
        user_agent="agent-a",
    )

    with pytest.raises(HTTPException):
        asyncio.run(verify_photo_download_token(token, user_agent="agent-b"))


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "photo_token_bind_ua", False)
    token = build_photo_download_token(
        org_id=settings.default_org_id,
        order_id="order-1",
        photo_id="photo-1",
        ttl_seconds=-60,
    )

    with pytest.raises(HTTPException):
        asyncio.run(verify_photo_download_token(token, user_agent=None))


class _FakeRedis:
    def __init__(self):
        self.used = False
        self.value = None

    async def set(self, key, value, ex=None, nx=False):
        if self.used:
            return False
        self.used = True
        self.value = (key, value, ex, nx)
        return True


def test_one_time_tokens_enforced(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(settings, "photo_token_bind_ua", False)
    monkeypatch.setattr(settings, "photo_token_one_time", True)
    monkeypatch.setattr(photo_tokens, "_redis_client", None)
    async def _fake_redis():
        return fake

    monkeypatch.setattr(photo_tokens, "_redis", _fake_redis)

    token = build_photo_download_token(
        org_id=uuid.uuid4(),
        order_id="order-1",
        photo_id="photo-1",
        ttl_seconds=120,
    )

    claims = asyncio.run(verify_photo_download_token(token, user_agent=None))
    assert claims.photo_id == "photo-1"

    with pytest.raises(HTTPException):
        asyncio.run(verify_photo_download_token(token, user_agent=None))


def test_tokens_accept_missing_user_agent_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "photo_token_bind_ua", False)
    token = build_photo_download_token(
        org_id=settings.default_org_id,
        order_id="order-1",
        photo_id="photo-1",
    )

    claims = asyncio.run(verify_photo_download_token(token, user_agent=None))
    assert claims.order_id == "order-1"
