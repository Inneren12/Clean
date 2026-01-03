from starlette.requests import Request

from app.api.entitlements import resolve_org_id
from app.infra.org_context import get_current_org_id, set_current_org_id
from app.settings import settings


def test_default_org_context_is_retained_without_identity():
    set_current_org_id(None)
    request = Request({"type": "http", "headers": []})

    resolved = resolve_org_id(request)

    assert resolved == settings.default_org_id
    assert get_current_org_id() == settings.default_org_id
