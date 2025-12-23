from app.main import app


def test_rate_limit_returns_problem_details(client):
    limiter = app.state.rate_limiter
    previous_limit = limiter.requests_per_minute
    limiter.reset()
    limiter.requests_per_minute = 1
    try:
        first = client.get("/healthz")
        assert first.status_code == 200
        second = client.get("/healthz")
        assert second.status_code == 429
        body = second.json()
        assert body["title"] == "Too Many Requests"
        assert body["type"].endswith("rate-limit")
        assert body["request_id"]
    finally:
        limiter.requests_per_minute = previous_limit
        limiter.reset()
