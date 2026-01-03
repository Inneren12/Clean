import json
import logging

import pytest

from app.main import app
from app.infra.logging import configure_logging


def _remove_route(path: str) -> None:
    app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != path]


def test_logging_redacts_tokens_and_signed_urls(capsys):
    configure_logging()
    logger = logging.getLogger("pii-test")

    logger.info(
        "sensitive log",
        extra={
            "authorization": "Bearer super-secret",
            "extra": {"url": "https://example.com/download?token=abc123&signature=signed"},
        },
    )

    captured = capsys.readouterr()
    stream = (captured.out or captured.err).strip().splitlines()
    assert stream
    payload = json.loads(stream[-1])

    assert payload["authorization"] == "[REDACTED]"
    assert "abc123" not in payload["url"]
    assert "signed" not in payload["url"]
    assert "[REDACTED_TOKEN]" in payload["url"]


def test_request_id_present_in_logs_and_response(client_no_raise, capsys):
    configure_logging()

    async def boom():  # pragma: no cover - executed via HTTP
        raise RuntimeError("boom")

    route_path = "/boom-log"
    app.router.add_api_route(route_path, boom, methods=["GET"])

    response = client_no_raise.get(route_path, headers={"X-Request-ID": "req-123"})

    assert response.status_code == 500
    assert response.json()["request_id"] == "req-123"
    captured = capsys.readouterr()
    combined_stream = (captured.out + captured.err).strip().splitlines()
    assert combined_stream
    unhandled_line = next(line for line in reversed(combined_stream) if "unhandled_exception" in line)
    log_payload = json.loads(unhandled_line)
    assert log_payload.get("request_id") == "req-123"
    _remove_route(route_path)
