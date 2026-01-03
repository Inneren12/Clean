import uuid

from fastapi import Depends, HTTPException, Request, status

from app.api.saas_auth import SaaSIdentity, _get_cached_identity, _get_saas_token
from app.infra.org_context import set_current_org_id
from app.settings import settings


async def require_org_context(
    request: Request, identity: SaaSIdentity | None = Depends(_get_cached_identity)
) -> uuid.UUID:
    token_present = _get_saas_token(request) is not None

    if identity:
        request.state.current_org_id = identity.org_id
        set_current_org_id(identity.org_id)
        return identity.org_id

    if token_present:
        error_from_state: HTTPException | None = getattr(request.state, "saas_identity_error", None)
        if error_from_state:
            raise error_from_state
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    if not settings.legacy_basic_auth_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    fallback_org = getattr(request.state, "current_org_id", None) or settings.default_org_id
    try:
        org_id = uuid.UUID(str(fallback_org))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid org context")

    request.state.current_org_id = org_id
    set_current_org_id(org_id)
    return org_id
