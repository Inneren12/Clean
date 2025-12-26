# Cloudflare deployment baseline

## Cloudflare Pages (web)
- Connect the `web/` folder as a Pages project. Build command: `npm ci && npm run build` (framework auto-detect).
- Set `NEXT_PUBLIC_API_BASE_URL` to your API origin (Container or other). Use the prod URL for production, and a preview/staging URL for preview deployments.
- If Turnstile is enabled, set `NEXT_PUBLIC_TURNSTILE_SITE_KEY` and mirror the server-side `CAPTCHA_MODE` and `TURNSTILE_SECRET_KEY` values in the API.
- For previews, lock CORS on the API to the Pages preview host; for production, lock to the custom domain.

## Cloudflare Containers (API)
- Build and push the API image (example):
  - `docker build -t registry.example.com/clean-api:latest .`
  - `docker push registry.example.com/clean-api:latest`
- Required runtime env vars (non-exhaustive): `DATABASE_URL`, `REDIS_URL` (for Redis limiter), `ADMIN_BASIC_USERNAME` / `ADMIN_BASIC_PASSWORD`, `DISPATCHER_BASIC_USERNAME` / `DISPATCHER_BASIC_PASSWORD`, export settings (`EXPORT_MODE`, `EXPORT_WEBHOOK_URL`, `EXPORT_WEBHOOK_ALLOWED_HOSTS` or `STRICT_CORS=true` in prod), captcha (`CAPTCHA_MODE`, `TURNSTILE_SECRET_KEY`), retention (`RETENTION_*`), Stripe keys if used.
- Set `CORS_ORIGINS` to your Pages domain(s) so browsers can call the API. In prod, keep `STRICT_CORS=true` and explicitly list origins.
- To keep data tidy, schedule `POST /v1/admin/retention/cleanup` via Cloudflare Scheduler or cron with basic auth.

## GitHub Actions outline
- A minimal deploy job (manual trigger) can build and push the container. Secrets stay in the repo settings:

```yaml
name: deploy-api
on:
  workflow_dispatch:
    inputs:
      image_tag:
        description: "Tag to deploy"
        required: true
jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      - name: Login to registry
        uses: docker/login-action@v3
        with:
          registry: ${{ secrets.REGISTRY_HOST }}
          username: ${{ secrets.REGISTRY_USERNAME }}
          password: ${{ secrets.REGISTRY_PASSWORD }}
      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ secrets.REGISTRY_HOST }}/clean-api:${{ github.event.inputs.image_tag }}
```
