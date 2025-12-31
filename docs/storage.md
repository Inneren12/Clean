# Object storage for uploads

This service can store order photos locally for development or in an S3-compatible bucket (AWS S3, Cloudflare R2, etc.) for production.

## Configuration

Set the storage backend via environment variables:

- `ORDER_STORAGE_BACKEND`: `local` (default) or `s3` (use `memory` for tests only).
- `ORDER_UPLOAD_ROOT`: local filesystem root for uploads when using the `local` backend.
- `S3_ENDPOINT`: Optional custom endpoint for S3-compatible services (e.g., R2).
- `S3_BUCKET`: Bucket name for uploads.
- `S3_ACCESS_KEY` / `S3_SECRET_KEY`: Credentials with write/delete permissions on the bucket.
- `S3_REGION`: Region identifier for the target bucket.
- `ORDER_PHOTO_SIGNED_URL_TTL`: Lifetime in seconds for generated signed download URLs (default: 600s).
- `ORDER_PHOTO_SIGNING_SECRET`: Optional override for HMAC signing of local download URLs (defaults to `AUTH_SECRET_KEY`).

The object key layout includes the organization ID and order ID to enforce tenant isolation:

```
orders/{org_id}/{order_id}/{random-filename}
```

## Download behaviour

- **Production** (`ORDER_STORAGE_BACKEND=s3`): API endpoints return short-lived presigned URLs. Downloads go directly to the bucket; the app is not in the hot path.
- **Local development** (`ORDER_STORAGE_BACKEND=local`): Files are saved under `ORDER_UPLOAD_ROOT`. Signed URLs point to the app's `/v1/orders/{order_id}/photos/{photo_id}/signed-download` endpoint and are HMAC-protected with a short TTL.
- Signed URLs are available for admins, workers, and clients via dedicated endpoints that enforce role permissions before issuing the link.

## Safety checks

- MIME types are restricted by `ORDER_PHOTO_ALLOWED_MIMES` and uploads are rejected if they exceed `ORDER_PHOTO_MAX_BYTES`.
- Paths are sanitized to prevent traversal; only alphanumeric, dash, underscore, and dot characters are allowed in path components.
- Signed links expire quickly (configurable TTL) and must include a valid signature when using the local backend.
