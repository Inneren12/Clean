from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter()


@router.get("/metrics")
async def metrics_endpoint(request: Request) -> Response:
    metrics_client = getattr(request.app.state, "metrics", None)
    if metrics_client is None or not getattr(metrics_client, "enabled", False):
        raise HTTPException(status_code=404, detail="Metrics disabled")

    app_settings = getattr(request.app.state, "app_settings", None)
    token = getattr(app_settings, "metrics_token", None) if app_settings else None
    if token:
        auth_header = request.headers.get("Authorization")
        if auth_header != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    payload, content_type = metrics_client.render()
    return Response(content=payload, media_type=content_type)
