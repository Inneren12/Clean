import asyncio
from types import SimpleNamespace

from app.infra.email import EmailAdapter
from app.settings import settings


def test_email_adapter_off_does_not_crash():
    original_mode = settings.email_mode
    settings.email_mode = "off"
    adapter = EmailAdapter()
    dummy_lead = SimpleNamespace(name="Test", email="customer@example.com", lead_id="123")
    try:
        asyncio.run(adapter.send_request_received(dummy_lead))
    finally:
        settings.email_mode = original_mode
