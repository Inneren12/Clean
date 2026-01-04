import logging
import random
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

import anyio
import httpx

from app.infra.metrics import metrics
from app.settings import settings
from app.shared.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

logger = logging.getLogger(__name__)


class NoopEmailAdapter:
    async def send_email(
        self, recipient: str, subject: str, body: str, headers: dict[str, str] | None = None
    ) -> bool:  # noqa: D401
        logger.info(
            "email_send_skipped",
            extra={"extra": {"recipient": recipient, "subject": subject, "mode": "noop"}},
        )
        metrics.record_email_adapter("skipped")
        return False

    async def send_request_received(self, lead: Any) -> None:  # pragma: no cover - passthrough
        await self.send_email(getattr(lead, "email", ""), "", "")

    async def send_booking_confirmed(self, recipient: str, context: dict[str, str] | None = None) -> None:  # noqa: D401
        await self.send_email(recipient, "", "")


class EmailAdapter:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.http_client = http_client
        self._breaker = CircuitBreaker(
            name="email",
            failure_threshold=settings.email_circuit_failure_threshold,
            recovery_time=settings.email_circuit_recovery_seconds,
        )

    async def send_email(
        self, recipient: str, subject: str, body: str, *, headers: dict[str, str] | None = None
    ) -> bool:
        if settings.email_mode == "off":
            metrics.record_email_adapter("skipped")
            return False
        if not recipient:
            metrics.record_email_adapter("skipped")
            return False
        try:
            await self._breaker.call(
                self._send_email, to_email=recipient, subject=subject, body=body, headers=headers
            )
        except CircuitBreakerOpenError:
            logger.warning("email_circuit_open", extra={"extra": {"recipient": recipient}})
            metrics.record_email_adapter("circuit_open")
            return False
        except Exception:
            metrics.record_email_adapter("error")
            raise
        metrics.record_email_adapter("sent")
        return True

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
            delivered = await self.send_email(recipient=lead.email, subject=subject, body=body)
            if delivered:
                logger.info(
                    "email_request_received_sent",
                    extra={"extra": {"lead_id": getattr(lead, "lead_id", None)}},
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "email_request_received_failed",
                extra={"extra": {"lead_id": getattr(lead, "lead_id", None), "reason": type(exc).__name__}},
            )

    async def send_booking_confirmed(self, recipient: str, context: dict[str, str] | None = None) -> None:
        subject = "Cleaning booking confirmed"
        body = "Your booking has been confirmed. Our crew will see you soon!"
        if context:
            notes = "\n".join(f"- {key}: {value}" for key, value in context.items())
            body = f"{body}\n\nDetails:\n{notes}"
        await self.send_email(recipient=recipient, subject=subject, body=body)

    async def _send_email(
        self, to_email: str, subject: str, body: str, headers: dict[str, str] | None = None
    ) -> None:
        if settings.email_mode == "sendgrid":
            await self._send_via_sendgrid(to_email=to_email, subject=subject, body=body, headers=headers)
            return
        if settings.email_mode == "smtp":
            await self._send_via_smtp(to_email=to_email, subject=subject, body=body, headers=headers)
            return
        raise RuntimeError("unsupported_email_mode")

    async def _send_via_sendgrid(
        self,
        to_email: str,
        subject: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
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
        if headers:
            payload["headers"] = headers
        client = self.http_client or httpx.AsyncClient()
        close_client = self.http_client is None
        try:
            response = await _post_with_retry(
                client,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        finally:
            if close_client:
                await client.aclose()
        if response.status_code >= 400:
            raise RuntimeError(f"sendgrid_status_{response.status_code}")

    async def _send_via_smtp(
        self, to_email: str, subject: str, body: str, *, headers: dict[str, str] | None = None
    ) -> None:
        host = settings.smtp_host
        port = settings.smtp_port or 587
        username = settings.smtp_username
        password = settings.smtp_password
        from_email = settings.email_sender
        if not host or not from_email:
            raise RuntimeError("smtp_not_configured")

        formatted_from = (
            formataddr((settings.email_from_name, from_email)) if settings.email_from_name else from_email
        )

        message = EmailMessage()
        message["From"] = formatted_from
        message["To"] = to_email
        message["Subject"] = subject
        if headers:
            for header_name, header_value in headers.items():
                message[header_name] = header_value
        message.set_content(body)

        def _send_blocking() -> None:
            if settings.smtp_use_tls:
                with smtplib.SMTP(host, port, timeout=settings.smtp_timeout_seconds) as smtp:
                    smtp.starttls()
                    if username and password:
                        smtp.login(username, password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP_SSL(host, port, timeout=settings.smtp_timeout_seconds) as smtp:
                    if username and password:
                        smtp.login(username, password)
                    smtp.send_message(message)

        await anyio.to_thread.run_sync(_send_blocking)


def resolve_email_adapter(app_settings) -> EmailAdapter | NoopEmailAdapter:
    if app_settings.email_mode == "off" or getattr(app_settings, "testing", False):
        return NoopEmailAdapter()
    return EmailAdapter()


def resolve_app_email_adapter(app_like) -> EmailAdapter | NoopEmailAdapter | None:
    state = getattr(app_like, "state", None)
    if state is None:
        return None
    app_state = getattr(getattr(app_like, "app", None), "state", None) or state
    adapter = getattr(app_state, "email_adapter", None)
    if adapter is not None:
        return adapter
    services = getattr(app_state, "services", None)
    if services is not None:
        return getattr(services, "email_adapter", None)
    return None


async def _post_with_retry(
    client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
) -> httpx.Response:
    max_attempts = settings.email_http_max_attempts
    base_backoff = settings.email_http_backoff_seconds
    max_backoff = settings.email_http_backoff_max_seconds
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers=headers,
                json=json,
                timeout=settings.email_timeout_seconds,
            )
            # Retry on 429 or 5xx
            if response.status_code in (429,) or response.status_code >= 500:
                if attempt < max_attempts:
                    delay = min(base_backoff * (2 ** (attempt - 1)), max_backoff)
                    jitter = delay * random.uniform(0.0, 0.3)
                    await anyio.sleep(delay + jitter)
                    continue
            return response
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < max_attempts:
                delay = min(base_backoff * (2 ** (attempt - 1)), max_backoff)
                jitter = delay * random.uniform(0.0, 0.3)
                await anyio.sleep(delay + jitter)
                continue
            raise
        except Exception:
            # Don't retry on other exceptions
            raise

    if last_exc:
        raise last_exc
    # This should not be reached, but for type safety
    raise RuntimeError("email_http_retry_exhausted")  # pragma: no cover
