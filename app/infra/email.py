import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import anyio
import httpx

from app.settings import settings

logger = logging.getLogger(__name__)


class EmailAdapter:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.http_client = http_client

    async def send_request_received(self, lead: Any) -> None:
        if settings.email_mode == "off":
            return
        if not getattr(lead, "email", None):
            return
        subject = "Cleaning request received"
        body = (
            f"Hi {getattr(lead, 'name', 'there')},\n\n"
            "Thanks for requesting a cleaning with us. "
            "Our operator will confirm your booking shortly over email.\n\n"
            "If you have any updates, just reply to this email."
        )
        try:
            await self._send_email(to_email=lead.email, subject=subject, body=body)
            logger.info("email_request_received_sent", extra={"extra": {"lead_id": getattr(lead, "lead_id", None)}})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "email_request_received_failed",
                extra={"extra": {"lead_id": getattr(lead, "lead_id", None), "reason": type(exc).__name__}},
            )

    async def send_booking_confirmed(self, recipient: str, context: dict[str, str] | None = None) -> None:
        if settings.email_mode == "off":
            return
        subject = "Cleaning booking confirmed"
        body = "Your booking has been confirmed. Our crew will see you soon!"
        if context:
            notes = "\n".join(f"- {key}: {value}" for key, value in context.items())
            body = f"{body}\n\nDetails:\n{notes}"
        await self._send_email(to_email=recipient, subject=subject, body=body)

    async def _send_email(self, to_email: str, subject: str, body: str) -> None:
        if settings.email_mode == "sendgrid":
            await self._send_via_sendgrid(to_email=to_email, subject=subject, body=body)
            return
        if settings.email_mode == "smtp":
            await self._send_via_smtp(to_email=to_email, subject=subject, body=body)
            return
        raise RuntimeError("unsupported_email_mode")

    async def _send_via_sendgrid(self, to_email: str, subject: str, body: str) -> None:
        api_key = settings.sendgrid_api_key
        from_email = settings.email_sender
        if not api_key or not from_email:
            raise RuntimeError("sendgrid_not_configured")
        payload = {
            "personalizations": [
                {
                    "to": [{"email": to_email}],
                }
            ],
            "from": {"email": from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        if settings.email_from_name:
            payload["from"]["name"] = settings.email_from_name
        client = self.http_client or httpx.AsyncClient()
        close_client = self.http_client is None
        try:
            response = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=10,
            )
        finally:
            if close_client:
                await client.aclose()
        if response.status_code >= 400:
            raise RuntimeError(f"sendgrid_status_{response.status_code}")

    async def _send_via_smtp(self, to_email: str, subject: str, body: str) -> None:
        host = settings.smtp_host
        port = settings.smtp_port or 587
        username = settings.smtp_username
        password = settings.smtp_password
        from_email = settings.email_sender
        if not host or not from_email:
            raise RuntimeError("smtp_not_configured")

        message = EmailMessage()
        message["From"] = from_email
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        def _send_blocking() -> None:
            if settings.smtp_use_tls:
                with smtplib.SMTP(host, port) as smtp:
                    smtp.starttls()
                    if username and password:
                        smtp.login(username, password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP_SSL(host, port) as smtp:
                    if username and password:
                        smtp.login(username, password)
                    smtp.send_message(message)

        await anyio.to_thread.run_sync(_send_blocking)
