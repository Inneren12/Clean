# Cloudflare deployment baseline

Fast path: keep the existing Docker-based API and the Next.js web, but deploy them to Cloudflare (Pages for the web UI, Containers for the API). The Postgres database remains external/managed.

## Cloudflare Pages (web)

Pick the path that matches how you build Next.js locally:

- **Option A: next-on-pages (recommended when you are not doing `next export`)**
  - Build command: `npx @cloudflare/next-on-pages@1`
  - Output directory: `.vercel/output/static`
- **Option B: static export** (use only if the project is configured for `next export` or `output: "export"`)
  - Build command: `npm ci && npm run build && npx next export`
  - Output directory: `out`

Notes:
- SSR-only features require Cloudflare Workers (use `@cloudflare/next-on-pages` or a full Workers deployment); Pages alone is for static output.
- Create the Pages project with the **root directory** set to `web/`.
- Production branch targets your production Pages domain; previews use the auto-generated preview URL.
- Environment variables:
  - `NEXT_PUBLIC_API_BASE_URL=https://<api-domain>`
  - `NEXT_PUBLIC_TURNSTILE_SITE_KEY` (only when `CAPTCHA_MODE=turnstile` on the API)
- Post-deploy checks:
  - Open the landing page ("/") on the preview/prod URL.
  - Open `/admin` (expects API basic auth credentials for admin endpoints).
  - Confirm network calls use `NEXT_PUBLIC_API_BASE_URL`.

## Cloudflare Containers (API)

Supported image sources: build from the repository Dockerfile and push to a registry Cloudflare Containers can reach. The canonical flow is to build/push to Amazon ECR via CI (see `.github/workflows/deploy_cloudflare.yml`), then point the Container app at that tag. Cloudflare’s own registry via Wrangler is also supported, but not wired here.

GitHub Actions secrets for the ECR push workflow:
- `AWS_REGION`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_ECR_REPOSITORY`

The registry URI is discovered from the ECR login step (`aws-actions/amazon-ecr-login@v2`); no explicit `AWS_ECR_REGISTRY` secret is required.

Requests reach the container through a minimal Worker proxy (Cloudflare’s default): a Worker receives the request and forwards it to the running container instance with `container.fetch(request)`.

Steps:
1. Build and push the Docker image.
   - Recommended: run the GitHub Action **Deploy to Cloudflare Containers (ECR push)** with the desired `image_tag`; it builds from the repository and pushes to ECR.
   - The resulting tag looks like `<account>.dkr.ecr.<region>.amazonaws.com/<repository>:<tag>`.
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
- Web build commands:
  - Option A: `npx @cloudflare/next-on-pages@1`
  - Option B: `npm ci && npm run build && npx next export`
