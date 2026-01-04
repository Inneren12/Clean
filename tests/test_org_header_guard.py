import uuid

import pytest
from starlette.requests import Request

from app.api.entitlements import resolve_org_id
from app.settings import settings


@pytest.mark.anyio
async def test_x_test_org_ignored_in_prod():
    original_testing = settings.testing
    original_env = settings.app_env
    original_default_org = settings.default_org_id

    default_org = uuid.uuid4()
    header_org = uuid.uuid4()

    settings.testing = False
    settings.app_env = "prod"
    settings.default_org_id = default_org

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-test-org", str(header_org).encode())],
        "query_string": b"",
        "client": ("test", 0),
        "server": ("testserver", 80),
        "scheme": "http",
    }

    try:
        request = Request(scope)
        resolved = resolve_org_id(request)
        assert resolved == default_org
        assert getattr(request.state, "current_org_id", None) == default_org
    finally:
        settings.testing = original_testing
        settings.app_env = original_env
        settings.default_org_id = original_default_org
