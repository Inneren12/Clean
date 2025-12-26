# Cloudflare deployment baseline

Fast path: keep the existing Docker-based API and Next.js web, but deploy them to Cloudflare (Pages for the web UI, Containers for the API). The Postgres database remains external/managed.

## Cloudflare Pages (web) steps
1. Create a new Pages project and point the **root directory** to `web/`.
2. Build command: `npm ci && npm run build` (Cloudflare auto-detects Next.js; do not override the output directory).
3. Production branch targets your production Pages domain; previews use the auto-generated preview URL.
4. Environment variables:
   - `NEXT_PUBLIC_API_BASE_URL=https://<api-domain>`
   - `NEXT_PUBLIC_TURNSTILE_SITE_KEY` (only when `CAPTCHA_MODE=turnstile` on the API)
5. Post-deploy checks:
   - Open the landing page ("/") on the preview/prod URL.
   - Open `/admin` (expects API basic auth credentials for admin endpoints).
   - Confirm network calls use `NEXT_PUBLIC_API_BASE_URL`.

## Cloudflare Containers (API) steps
1. Build and push the Docker image (replace registry/namespace):
   - `docker build -t <registry>/clean-api:<tag> .`
   - `docker push <registry>/clean-api:<tag>`
2. Create a Container app using that image tag.
3. Configure **port 8000** and health check path `/healthz`.
4. Add environment variables (leave secrets in Cloudflare, not in git):

| Name | Required | Notes |
| --- | --- | --- |
| `APP_ENV` | yes | `prod` in Cloudflare |
| `STRICT_CORS` | yes | `true` to block wildcards |
| `CORS_ORIGINS` | yes | Comma list of allowed origins (Pages preview + prod domains) |
| `DATABASE_URL` | yes | Postgres connection string (Cloudflare-managed or external) |
| `REDIS_URL` | optional | Needed for multi-instance rate limiting |
| `ADMIN_BASIC_USERNAME` / `ADMIN_BASIC_PASSWORD` | yes | For admin endpoints |
| `DISPATCHER_BASIC_USERNAME` / `DISPATCHER_BASIC_PASSWORD` | yes | For dispatcher endpoints |
| `EXPORT_MODE` | optional | `off`/`webhook`/`sheets`; default `off` |
| `EXPORT_WEBHOOK_URL` | optional | Required when `EXPORT_MODE=webhook` |
| `EXPORT_WEBHOOK_ALLOWED_HOSTS` | optional | Comma/JSON list of allowed webhook hosts |
| `CAPTCHA_MODE` | optional | `off` or `turnstile` |
| `TURNSTILE_SECRET_KEY` | optional | Required when `CAPTCHA_MODE=turnstile` |
| `RETENTION_*` | optional | `RETENTION_CHAT_DAYS`, `RETENTION_LEAD_DAYS`, `RETENTION_ENABLE_LEADS` |
| `STRIPE_*` | optional | Required if deposits are enabled (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`) |
| `EMAIL_MODE` + provider vars | optional | `sendgrid` or `smtp` with `EMAIL_FROM`, `EMAIL_FROM_NAME`, `SENDGRID_API_KEY` or `SMTP_*` |
| `PRICING_CONFIG_PATH` | yes | Defaults to `pricing/economy_v1.json` |
| `RATE_LIMIT_PER_MINUTE` | optional | Default `30` |
| `TRUST_PROXY_HEADERS` | recommended | `true` when behind Cloudflare proxy |
| `TRUSTED_PROXY_CIDRS` | recommended | Comma/JSON list of Cloudflare IP ranges (keep in sync with https://www.cloudflare.com/ips) |
| `TRUSTED_PROXY_IPS` | optional | Use for specific egress IPs if not using CIDRs |

5. Networking/proxy notes:
   - Enable `TRUST_PROXY_HEADERS=true` so rate limiting uses the real client IP from `CF-Connecting-IP`.
   - Populate `TRUSTED_PROXY_CIDRS` with Cloudflare's published IPv4/IPv6 ranges; avoid `*`/`0.0.0.0/0`.
   - Set `CORS_ORIGINS` explicitly (preview + prod Pages domains). Do not use `*` in production.

## CORS locked checklist
- `STRICT_CORS=true` in production.
- `CORS_ORIGINS` contains only the Cloudflare Pages preview URL(s) and the production domain(s).
- No wildcard origins configured on the API.
- Browser requests from other origins should fail the preflight.

## Verification commands
- Health: `curl https://<api-domain>/healthz`
- CORS preflight sample: `curl -i -X OPTIONS https://<api-domain>/v1/estimate -H "Origin: https://<pages-domain>" -H "Access-Control-Request-Method: POST"`
  - Expect `200` with `access-control-allow-origin` matching your origin when it is in `CORS_ORIGINS`.

## Rollback
- Redeploy the previous container image tag in Cloudflare Containers.
- Verify `/healthz` and a sample API call (`/v1/estimate` or `/v1/leads`) before switching traffic if you use staged rollouts.

## Appendix: runtime assumptions
- The API container starts with `uvicorn app.main:app --host 0.0.0.0 --port 8000` (see `Dockerfile`).
- Health endpoint: `GET /healthz`.
- Web build command validated: `npm ci && npm run build` from `web/`.
