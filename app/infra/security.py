import time
from ipaddress import ip_address, ip_network
from collections import defaultdict, deque
from typing import Deque, Dict

from starlette.requests import Request


class RateLimiter:
    def __init__(self, requests_per_minute: int, cleanup_minutes: int = 10) -> None:
        self.requests_per_minute = requests_per_minute
        self.cleanup_minutes = cleanup_minutes
        self._requests: Dict[str, Deque[float]] = defaultdict(deque)
        self._last_seen: Dict[str, float] = {}
        self._last_prune: float = 0.0

    def allow(self, key: str) -> bool:
        now = time.time()
        self._maybe_prune(now)
        window_start = now - 60
        timestamps = self._requests[key]
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()
        if len(timestamps) >= self.requests_per_minute:
            self._last_seen[key] = now
            return False
        timestamps.append(now)
        self._last_seen[key] = now
        return True

    def reset(self) -> None:
        self._requests.clear()
        self._last_seen.clear()
        self._last_prune = 0.0

    def _maybe_prune(self, now: float) -> None:
        if now - self._last_prune < 60:
            return
        expire_before = now - (self.cleanup_minutes * 60)
        for key in list(self._requests.keys()):
            timestamps = self._requests[key]
            if not timestamps or self._last_seen.get(key, 0.0) < expire_before:
                self._requests.pop(key, None)
                self._last_seen.pop(key, None)
        self._last_prune = now


def resolve_client_key(
    request: Request,
    trust_proxy_headers: bool,
    trusted_proxy_ips: list[str],
    trusted_proxy_cidrs: list[str],
) -> str:
    client_host = request.client.host if request.client else "unknown"
    if not trust_proxy_headers or not _is_trusted_proxy(client_host, trusted_proxy_ips, trusted_proxy_cidrs):
        return client_host
    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return client_host
    first_ip = forwarded_for.split(",")[0].strip()
    try:
        ip_address(first_ip)
    except ValueError:
        return client_host
    return first_ip


def _is_trusted_proxy(client_host: str, trusted_ips: list[str], trusted_cidrs: list[str]) -> bool:
    if client_host in trusted_ips:
        return True
    try:
        client_ip = ip_address(client_host)
    except ValueError:
        return False
    for cidr in trusted_cidrs:
        try:
            if client_ip in ip_network(cidr):
                return True
        except ValueError:
            continue
    return False
