# Object storage for uploads

This service can store order photos locally for development or in an S3-compatible bucket (AWS S3, Cloudflare R2, etc.) for production.

## Configuration

Set the storage backend via environment variables:

- `ORDER_STORAGE_BACKEND`: `local` (default), `s3`, `r2`, `cloudflare_r2`, or `cloudflare_images` (use `memory` for tests only).
- `ORDER_UPLOAD_ROOT`: local filesystem root for uploads when using the `local` backend.
- `S3_ENDPOINT`: Optional custom endpoint for S3-compatible services (e.g., R2).
- `S3_BUCKET`: Bucket name for uploads.
- `S3_ACCESS_KEY` / `S3_SECRET_KEY`: Credentials with write/delete permissions on the bucket.
- `S3_REGION`: Region identifier for the target bucket.
- `PHOTO_URL_TTL_SECONDS`: Lifetime for signed download URLs (default: 60s). Also caps downstream presigned URL TTL.
- `PHOTO_DOWNLOAD_REDIRECT_STATUS`: HTTP status code used when redirecting to provider URLs (302/307).
- `PHOTO_TOKEN_SECRET`: HMAC secret for download tokens (falls back to `ORDER_PHOTO_SIGNING_SECRET` or `AUTH_SECRET_KEY`).
- `PHOTO_TOKEN_BIND_UA`: Whether to bind download tokens to the requesting User-Agent (default: true).
- `PHOTO_TOKEN_ONE_TIME`: If true and Redis is configured, download tokens are single-use (default: false).
- `ORDER_PHOTO_SIGNING_SECRET`: Optional override for HMAC signing of local download URLs (defaults to `AUTH_SECRET_KEY`).

For **Cloudflare Images** uploads (`ORDER_STORAGE_BACKEND=cloudflare_images`):

- `CF_IMAGES_ACCOUNT_ID`: Cloudflare account ID used for the Images API.
- `CF_IMAGES_API_TOKEN`: Bearer token with Images read/write permissions.
- `CF_IMAGES_ACCOUNT_HASH`: Hash used by `imagedelivery.net` for public delivery URLs.
- `CF_IMAGES_DEFAULT_VARIANT`: Variant name used when redirecting downloads (e.g., `public`, `original`).
- `CF_IMAGES_THUMBNAIL_VARIANT` (optional): Variant name for admin gallery thumbnails (falls back to the default variant).
- `CF_IMAGES_SIGNING_KEY`: Required for private image delivery; used to sign exp/sig query params for `imagedelivery.net`.

The object key layout includes the organization ID and order ID to enforce tenant isolation:

```
orders/{org_id}/{order_id}/{random-filename}
```

## Download behaviour

- **Production (S3/R2)**: API endpoints mint a short-lived app token and redirect to a presigned GET URL. Tokens bind to org/order/photo and can be one-time when Redis is available.
- **Local development** (`ORDER_STORAGE_BACKEND=local`): Files are saved under `ORDER_UPLOAD_ROOT`. App download URLs are HMAC-protected with a short TTL and return the file with `Cache-Control: no-store` headers.
- **Cloudflare Images**: Uploads are created with `requireSignedURLs=true` and downloads redirect to `https://imagedelivery.net/{ACCOUNT_HASH}/{image_id}/{variant}?exp=...&sig=...` where `sig` is HMAC-SHA256 over the path and query using `CF_IMAGES_SIGNING_KEY`.
- Signed URLs are available for admins, workers, and clients via dedicated endpoints that enforce role permissions before issuing the link. The endpoints return the internal download URL (not the provider URL).

## Safety checks

- MIME types are restricted by `ORDER_PHOTO_ALLOWED_MIMES` and uploads are rejected if they exceed `ORDER_PHOTO_MAX_BYTES`.
- Paths are sanitized to prevent traversal; only alphanumeric, dash, underscore, and dot characters are allowed in path components.
- Signed links expire quickly (configurable TTL) and are bound to the organization/order/photo, optionally the User-Agent, and optionally one-time when Redis is present.
