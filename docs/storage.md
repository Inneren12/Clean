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
- `ORDER_PHOTO_SIGNED_URL_TTL`: Lifetime in seconds for generated signed download URLs (default: 600s).
- `ORDER_PHOTO_SIGNING_SECRET`: Optional override for HMAC signing of local download URLs (defaults to `AUTH_SECRET_KEY`).

For **Cloudflare Images** uploads (`ORDER_STORAGE_BACKEND=cloudflare_images`):

- `CF_IMAGES_ACCOUNT_ID`: Cloudflare account ID used for the Images API.
- `CF_IMAGES_API_TOKEN`: Bearer token with Images read/write permissions.
- `CF_IMAGES_ACCOUNT_HASH`: Hash used by `imagedelivery.net` for public delivery URLs.
- `CF_IMAGES_DEFAULT_VARIANT`: Variant name used when redirecting downloads (e.g., `public`, `original`).
- `CF_IMAGES_THUMBNAIL_VARIANT` (optional): Variant name for admin gallery thumbnails (falls back to the default variant).
- `CF_IMAGES_SIGNING_KEY` (optional): Provide if using signed delivery links managed on Cloudflare.

The object key layout includes the organization ID and order ID to enforce tenant isolation:

```
orders/{org_id}/{order_id}/{random-filename}
```

## Download behaviour

- **Production (S3/R2)**: API endpoints return short-lived presigned URLs. Downloads go directly to the bucket; the app is not in the hot path.
- **Local development** (`ORDER_STORAGE_BACKEND=local`): Files are saved under `ORDER_UPLOAD_ROOT`. Signed URLs point to the app's `/v1/orders/{order_id}/photos/{photo_id}/signed-download` endpoint and are HMAC-protected with a short TTL.
- **Cloudflare Images**: Uploads are pushed to the Images API and downloads are served via redirects to `https://imagedelivery.net/{ACCOUNT_HASH}/{image_id}/{variant}`. Signed URLs are not required for delivery; backend failures are surfaced as 5xx responses.
- Signed URLs are available for admins, workers, and clients via dedicated endpoints that enforce role permissions before issuing the link.

## Safety checks

- MIME types are restricted by `ORDER_PHOTO_ALLOWED_MIMES` and uploads are rejected if they exceed `ORDER_PHOTO_MAX_BYTES`.
- Paths are sanitized to prevent traversal; only alphanumeric, dash, underscore, and dot characters are allowed in path components.
- Signed links expire quickly (configurable TTL) and must include a valid signature when using the local backend.
