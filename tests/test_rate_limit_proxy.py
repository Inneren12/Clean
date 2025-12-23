from starlette.requests import Request

from app.infra.security import resolve_client_key


def _make_request(client_host: str, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/healthz",
        "headers": headers,
        "client": (client_host, 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


def test_resolve_client_key_honors_trusted_proxy():
    request = _make_request("203.0.113.5", "198.51.100.10")
    client = resolve_client_key(
        request,
        trust_proxy_headers=True,
        trusted_proxy_ips=["203.0.113.5"],
        trusted_proxy_cidrs=[],
    )
    assert client == "198.51.100.10"


def test_resolve_client_key_ignores_untrusted_proxy():
    request = _make_request("203.0.113.5", "198.51.100.10")
    client = resolve_client_key(
        request,
        trust_proxy_headers=True,
        trusted_proxy_ips=[],
        trusted_proxy_cidrs=[],
    )
    assert client == "203.0.113.5"
