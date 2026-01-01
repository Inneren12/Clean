import secrets

from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter()


@router.get("/metrics")
async def metrics_endpoint(request: Request) -> Response:
    metrics_client = getattr(request.app.state, "metrics", None)
    if metrics_client is None or not getattr(metrics_client, "enabled", False):
        raise HTTPException(status_code=404, detail="Metrics disabled")

    app_settings = getattr(request.app.state, "app_settings", None)
    token = getattr(app_settings, "metrics_token", None) if app_settings else None
    require_token = (app_settings.app_env == "prod") if app_settings else False
    if require_token:
        if not token:
            raise HTTPException(status_code=500, detail="Metrics token misconfigured")
        auth_header = request.headers.get("Authorization")
        provided = None
        if auth_header and auth_header.lower().startswith("bearer "):
            provided = auth_header.split(" ", 1)[1]
        if provided is None:
            provided = request.query_params.get("token")
        if not provided or not secrets.compare_digest(provided, token):
            raise HTTPException(status_code=401, detail="Unauthorized")

    payload, content_type = metrics_client.render()
    return Response(content=payload, media_type=content_type)
