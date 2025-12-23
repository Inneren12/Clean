import asyncio
import logging

import httpx

from app.infra.export import export_lead_async, validate_webhook_url
from app.settings import settings


def test_export_webhook_success_logs_and_returns(caplog):
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_allowed_hosts = settings.export_webhook_allowed_hosts
    original_block_private_ips = settings.export_webhook_block_private_ips

    settings.export_mode = "webhook"
    settings.export_webhook_url = "https://hook.test/lead"
    settings.export_webhook_allowed_hosts = ["hook.test"]
    settings.export_webhook_block_private_ips = True

    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    resolver = lambda host: ["8.8.8.8"]

    try:
        caplog.set_level(logging.INFO)
        asyncio.run(export_lead_async({"lead_id": "lead-123"}, transport=transport, resolver=resolver))
        assert any(record.message == "export_webhook_success" for record in caplog.records)
    finally:
        settings.export_mode = original_mode
        settings.export_webhook_url = original_url
        settings.export_webhook_allowed_hosts = original_allowed_hosts
        settings.export_webhook_block_private_ips = original_block_private_ips


def test_export_webhook_retries_and_fails_gracefully(caplog):
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_allowed_hosts = settings.export_webhook_allowed_hosts
    original_block_private_ips = settings.export_webhook_block_private_ips
    original_retries = settings.export_webhook_max_retries
    original_backoff = settings.export_webhook_backoff_seconds

    settings.export_mode = "webhook"
    settings.export_webhook_url = "https://hook.test/lead"
    settings.export_webhook_allowed_hosts = ["hook.test"]
    settings.export_webhook_block_private_ips = True
    settings.export_webhook_max_retries = 2
    settings.export_webhook_backoff_seconds = 0

    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    resolver = lambda host: ["8.8.8.8"]

    try:
        caplog.set_level(logging.WARNING)
        asyncio.run(export_lead_async({"lead_id": "lead-456"}, transport=transport, resolver=resolver))
        assert any(record.message == "export_webhook_failed" for record in caplog.records)
    finally:
        settings.export_mode = original_mode
        settings.export_webhook_url = original_url
        settings.export_webhook_allowed_hosts = original_allowed_hosts
        settings.export_webhook_block_private_ips = original_block_private_ips
        settings.export_webhook_max_retries = original_retries
        settings.export_webhook_backoff_seconds = original_backoff


def test_validate_webhook_url_rules():
    original_allowed_hosts = settings.export_webhook_allowed_hosts
    original_allow_http = settings.export_webhook_allow_http
    original_block_private_ips = settings.export_webhook_block_private_ips
    original_app_env = settings.app_env

    settings.export_webhook_allowed_hosts = ["allowed.test"]
    settings.export_webhook_allow_http = False
    settings.export_webhook_block_private_ips = True
    settings.app_env = "prod"

    resolver_public = lambda host: ["8.8.8.8"]
    resolver_private = lambda host: ["127.0.0.1"]

    try:
        ok, _ = validate_webhook_url("http://allowed.test/hook", resolver=resolver_public)
        assert not ok
        ok, _ = validate_webhook_url("https://unlisted.test/hook", resolver=resolver_public)
        assert not ok
        ok, _ = validate_webhook_url("https://127.0.0.1/hook", resolver=resolver_private)
        assert not ok
        ok, _ = validate_webhook_url("https://allowed.test/hook", resolver=resolver_public)
        assert ok
    finally:
        settings.export_webhook_allowed_hosts = original_allowed_hosts
        settings.export_webhook_allow_http = original_allow_http
        settings.export_webhook_block_private_ips = original_block_private_ips
        settings.app_env = original_app_env
