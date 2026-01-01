# Incident playbook — Storage errors

## Signal

- Upload/download failures; object storage alarms; `storage_bytes` metrics stop increasing.
- App logs show S3 timeouts or signature errors.

## Containment

1. Run `./scripts/chaos/s3_degraded.sh` in staging to mirror the failure pattern.
2. If S3 is down, switch upload flows to local backend temporarily (`ORDER_STORAGE_BACKEND=local`, invalidate signed URL cache).

## Mitigation

- Confirm credentials and bucket policy. Rotate keys if `SignatureDoesNotMatch` appears.
- Reduce payload size limits (`S3_MAX_PAYLOAD_BYTES`) if large uploads are saturating the pipe.
- Retry failed uploads after service restoration; resend signed URLs to users when necessary.

## Verification

- New uploads succeed; signed GET URLs download correctly.
- `storage_upload_latency_ms`/`storage_download_latency_ms` trends fall back to baseline in k6.
- No new `storage_bytes` entitlement violations logged.
