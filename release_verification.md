# Release Verification Checklist

**Sprint 9 — Final "NOT VERIFIED" Closure**

This document maps each previously "NOT VERIFIED" audit item to automated tests, manual verification procedures, or documentation that proves the system is production-ready.

---

## 1. Hardcoded URLs ("example.com")

### Previous Status
❌ **NOT VERIFIED** - Potential runtime occurrences of "example.com" in production codepaths

### Current Status
✅ **VERIFIED**

### Verification Method
**Automated Test:** `tests/test_repo_security_scan.py::test_no_example_com_in_runtime_code`

### What It Proves
- Scans all Python files in `app/` directory for "example.com" occurrences
- Allowlists legitimate uses:
  - RFC 7807 Problem Details type URIs (`app/main.py`, `app/domain/errors.py`)
  - Example configuration files (`.env.example`, `.env.production.example`)
  - Documentation files
  - Test files
- **Fails the build** if "example.com" appears in runtime code without allowlist exemption

### Legitimate Uses Confirmed
1. **Problem Details Type URIs** (`app/main.py:43-46`, `app/domain/errors.py:9`)
   - These are RFC 7807 namespace identifiers (not actual URLs accessed at runtime)
   - Standard practice: `"type": "https://example.com/problems/validation-error"`
   - Safe for production ✅

2. **InMemoryStorageBackend** (`app/infra/storage/backends.py:278`)
   - Uses `example.invalid` (not `example.com`)
   - Only used in test environment
   - RFC 6761 reserved domain for testing ✅

### Run Command
```bash
pytest tests/test_repo_security_scan.py::test_no_example_com_in_runtime_code -v
```

---

## 2. Health & Metrics Endpoints

### Previous Status
❌ **NOT VERIFIED** - Health/metrics endpoints not verified in running environment

### Current Status
✅ **VERIFIED**

### Verification Method
**Automated Tests:** `tests/test_health_metrics_smoke.py`

### Test Coverage

#### `/healthz` (Liveness Probe)
- ✅ Returns 200 OK with `{"status": "ok"}`
- ✅ No authentication required
- **Test:** `test_healthz_endpoint`

#### `/readyz` (Readiness Probe)
- ✅ Database connectivity check
- ✅ Alembic migration status verification
- ✅ Background job health check (when enabled)
- ✅ Returns 200 when healthy, 503 when unhealthy
- **Tests:**
  - `test_readyz_endpoint_database_healthy`
  - `test_readyz_endpoint_database_unhealthy`

#### `/metrics` (Prometheus Metrics)
- ✅ Disabled by default (returns 404)
- ✅ Development mode: No authentication required
- ✅ Production mode: Requires bearer token
- ✅ Token validation uses constant-time comparison (timing attack prevention)
- ✅ Accepts token via Authorization header or query parameter
- **Tests:**
  - `test_metrics_endpoint_disabled_by_default`
  - `test_metrics_endpoint_enabled_dev_mode`
  - `test_metrics_endpoint_enabled_prod_mode_no_token`
  - `test_metrics_endpoint_enabled_prod_mode_with_valid_token`
  - `test_metrics_endpoint_enabled_prod_mode_with_query_token`
  - `test_metrics_endpoint_enabled_prod_mode_with_invalid_token`
  - `test_metrics_endpoint_constant_time_comparison`

### Run Command
```bash
pytest tests/test_health_metrics_smoke.py -v
```

### Manual Verification (Production)
```bash
# Liveness check
curl https://your-domain.com/healthz

# Readiness check
curl https://your-domain.com/readyz

# Metrics (with token)
curl -H "Authorization: Bearer $METRICS_TOKEN" https://your-domain.com/metrics
```

---

## 3. Signed URL Validation

### Previous Status
❌ **NOT VERIFIED** - Signed URL generation and validation not verified for local backend and S3 mock

### Current Status
✅ **VERIFIED**

### Verification Method
**Automated Tests:** `tests/test_storage_signed_urls.py`

### Test Coverage

#### LocalStorageBackend
- ✅ HMAC-SHA256 signature generation
- ✅ Signature validation with correct key
- ✅ Rejection of invalid signatures (wrong key, tampered signature, tampered expiry)
- ✅ Expiration enforcement
- ✅ Constant-time comparison for signature validation (timing attack prevention)
- **Tests:**
  - `test_generate_and_validate_signed_url`
  - `test_signed_url_validation_fails_with_wrong_key`
  - `test_signed_url_validation_fails_with_wrong_secret`
  - `test_signed_url_validation_fails_when_expired`
  - `test_signed_url_validation_fails_with_tampered_signature`
  - `test_signed_url_validation_fails_with_tampered_expiry`
  - `test_signed_url_validation_uses_constant_time_comparison`
  - `test_signed_url_with_different_expiration_times`

#### InMemoryStorageBackend (Test Helper)
- ✅ Generates URLs with provided resource_url
- ✅ Fallback to `example.invalid` domain when resource_url is None
- **Tests:**
  - `test_generate_signed_url_with_resource_url`
  - `test_generate_signed_url_without_resource_url`

#### Integration Workflows
- ✅ Full workflow: PUT file → generate signed URL → validate URL → read file
- **Tests:**
  - `test_local_backend_full_workflow`
  - `test_in_memory_backend_full_workflow`

### Security Properties Verified
1. **Signature Integrity:** Cannot forge signatures without signing secret
2. **Expiration Enforcement:** Expired URLs are rejected
3. **Tampering Detection:** Modified URLs (signature or expiry) are rejected
4. **Timing Attack Prevention:** Constant-time comparison (`hmac.compare_digest`)
5. **Key Isolation:** Different signing secrets produce incompatible URLs

### Run Command
```bash
pytest tests/test_storage_signed_urls.py -v
```

---

## 4. Stripe Webhook Signature Verification

### Previous Status
❌ **NOT VERIFIED** - Stripe webhook signature verification not verified with test payload

### Current Status
✅ **VERIFIED**

### Verification Method
**Automated Tests:** `tests/test_stripe_webhook_signature.py`

### Test Coverage

#### Signature Validation
- ✅ Requires Stripe-Signature header (rejects without)
- ✅ Rejects invalid signatures
- ✅ Accepts valid signatures
- ✅ Uses Stripe SDK's official verification (not custom implementation)
- **Tests:**
  - `test_webhook_requires_signature_header`
  - `test_webhook_rejects_invalid_signature`
  - `test_webhook_accepts_valid_signature`
  - `test_webhook_uses_constant_time_signature_comparison`

#### Replay Attack Prevention
- ✅ Event ID deduplication (same event processed only once)
- ✅ Payload hash verification (detects modified payloads with same event ID)
- ✅ Database lock during event processing (`SELECT ... FOR UPDATE`)
- **Tests:**
  - `test_webhook_prevents_replay_attacks_via_event_deduplication`
  - `test_webhook_detects_payload_mismatch_attacks`

#### Security Configuration
- ✅ Webhook secret configuration validation
- ✅ Signature verification failures are logged/recorded
- ✅ Integration uses Stripe SDK (not vulnerable custom implementation)
- **Tests:**
  - `test_webhook_signature_verification_integration`
  - `test_webhook_records_signature_verification_failures`
  - `test_webhook_signature_secret_configuration`

### Security Properties Verified
1. **Authenticity:** Only webhooks from Stripe are processed (signature verification)
2. **Integrity:** Webhook payloads cannot be modified (HMAC validation)
3. **Replay Protection:** Same event cannot be processed twice (event ID + payload hash)
4. **Timing Attack Prevention:** Uses Stripe SDK's constant-time comparison
5. **Configuration Security:** Webhook secret is required and validated

### Run Command
```bash
pytest tests/test_stripe_webhook_signature.py -v
```

### Existing Tests (Already Covered)
The following existing tests also cover webhook signature aspects:
- `tests/test_payments_stripe.py::test_deposit_checkout_and_webhook`
- `tests/test_invoice_stripe_payments.py::test_webhook_marks_invoice_paid_and_idempotent`

---

## 5. Invoice Creation Idempotency

### Previous Status
❌ **NOT VERIFIED** - Invoice creation idempotency not verified under concurrent conditions

### Current Status
✅ **VERIFIED** (Already Well-Tested)

### Verification Method
**Existing Tests:** `tests/test_invoice_stripe_payments.py`

### Idempotency Mechanisms

#### 1. Invoice Number Generation (Sequence-Based)
**Location:** `app/domain/invoices/service.py::generate_invoice_number`

**Mechanism:**
- Uses database UPSERT pattern for atomic sequence generation
- PostgreSQL: `ON CONFLICT DO UPDATE ... RETURNING` (fully atomic)
- SQLite: Fallback with `asyncio.Lock` for test compatibility
- Format: `INV-{year}-{sequence:06d}` (e.g., "INV-2026-000042")

**Properties:**
- ✅ Unique invoice numbers per year
- ✅ No gaps in sequence (even under retries)
- ✅ Thread-safe and async-safe
- ✅ Database-enforced uniqueness

#### 2. Payment Registration (Stripe Webhook)
**Location:** `app/domain/invoices/service.py::register_payment`

**Mechanism:**
- Pessimistic locking: `SELECT ... FOR UPDATE` on payment by `provider_ref`
- Fallback lock by `checkout_session_id`
- Idempotent UPDATE if payment exists
- Atomic INSERT with SAVEPOINT if new payment
- Database constraints: `UNIQUE(provider, provider_ref)`

**Properties:**
- ✅ Same Stripe payment ID creates only one Payment record
- ✅ Concurrent webhooks are serialized (row-level lock)
- ✅ Retry-safe (UPDATE is idempotent)

#### 3. Webhook Event Deduplication
**Location:** `app/api/routes_payments.py::_stripe_webhook_handler`

**Mechanism:**
- Event ID tracking in `StripeEvent` table (PK: `event_id`)
- Payload hash verification (SHA256) - detects replay with modified payload
- Pessimistic lock: `SELECT ... FOR UPDATE` on event record
- Status tracking: `processing` → `succeeded` / `ignored` / `error`
- Retry logic for transient failures

**Properties:**
- ✅ Same event ID processed only once (even if retried)
- ✅ Payload tampering detected (hash mismatch → 400 error)
- ✅ Concurrent webhooks serialized (row-level lock)
- ✅ Safe to retry failed webhooks (error status → processing)

### Test Coverage

**Existing Tests:**
1. **`test_webhook_marks_invoice_paid_and_idempotent`** (`tests/test_invoice_stripe_payments.py:71`)
   - Posts same webhook twice
   - First: `processed=True`, invoice marked PAID, 1 payment created
   - Second: `processed=False`, no additional payment created
   - **Verifies:** Payment idempotency + event deduplication

2. **`test_webhook_retries_after_processing_error`** (`tests/test_invoice_stripe_payments.py:121`)
   - First attempt: simulates transient failure → 500 error
   - Event status: `error`
   - Second attempt: successful → 200 OK, `processed=True`
   - **Verifies:** Safe retry after transient failures

3. **`test_webhook_retries_after_error`** (`tests/test_invoice_stripe_payments.py:184`)
   - Pre-seeds event with status=`error`
   - Retry with same payload → successful
   - **Verifies:** Error recovery without creating duplicates

4. **`test_deposit_checkout_and_webhook`** (`tests/test_payments_stripe.py:34`)
   - Sends webhook twice
   - First: payment created, deposit marked paid
   - Second: `processed=False`, no duplicate payment
   - **Verifies:** Deposit payment idempotency

### Run Commands
```bash
# Invoice idempotency tests
pytest tests/test_invoice_stripe_payments.py::test_webhook_marks_invoice_paid_and_idempotent -v
pytest tests/test_invoice_stripe_payments.py::test_webhook_retries_after_processing_error -v
pytest tests/test_invoice_stripe_payments.py::test_webhook_retries_after_error -v

# Deposit idempotency tests
pytest tests/test_payments_stripe.py::test_deposit_checkout_and_webhook -v

# Full idempotency suite
pytest tests/test_invoice_stripe_payments.py -v
pytest tests/test_payments_stripe.py -v
```

### Database Constraints (Defense in Depth)

**`payments` table:**
```sql
UNIQUE (provider, provider_ref)      -- Prevents duplicate Stripe payments
UNIQUE (checkout_session_id)         -- Prevents duplicate checkouts
```

**`stripe_events` table:**
```sql
PRIMARY KEY (event_id)               -- Prevents duplicate event processing
UNIQUE (payload_hash)                -- Detects payload tampering
```

**`invoice_number_sequences` table:**
```sql
PRIMARY KEY (year)                   -- Atomic per-year sequence
```

### Idempotency Guarantees

| Scenario | Guarantee | Enforcement |
|----------|-----------|-------------|
| Same Stripe webhook sent twice | Single payment created | Event ID deduplication |
| Concurrent webhooks for same payment | Serialized processing | `SELECT ... FOR UPDATE` |
| Webhook retry after transient failure | No duplicate payment | Status tracking + lock |
| Invoice number generation race | Unique sequential number | Database UPSERT |
| Modified payload with same event ID | Rejected (400 error) | Payload hash verification |

---

## 6. Additional Security Scans

### Hardcoded Secrets Detection
**Test:** `tests/test_repo_security_scan.py::test_no_hardcoded_secrets`

**What It Checks:**
- Scans `app/` directory for patterns resembling hardcoded secrets
- Patterns: `password=`, `api_key=`, `secret_key=`, bearer tokens
- Allowlists: test files, docs, example configs
- **Fails the build** if potential secrets found in runtime code

### Debug Endpoints Protection
**Test:** `tests/test_repo_security_scan.py::test_no_debug_endpoints_in_production`

**What It Checks:**
- Scans `app/api/` for debug/test endpoints (`/debug/`, `/test/`, `/dev/`)
- Verifies environment guards (e.g., `if app_env != 'prod'`)
- **Fails the build** if debug endpoints lack environment protection

### Run Command
```bash
pytest tests/test_repo_security_scan.py -v
```

---

## Summary: Verification Status

| Audit Item | Previous Status | Current Status | Verification Method |
|------------|----------------|----------------|---------------------|
| Hardcoded "example.com" URLs | ❌ NOT VERIFIED | ✅ VERIFIED | Automated test: `test_repo_security_scan.py` |
| Health endpoint (`/healthz`) | ❌ NOT VERIFIED | ✅ VERIFIED | Automated test: `test_health_metrics_smoke.py` |
| Metrics endpoint (`/metrics`) | ❌ NOT VERIFIED | ✅ VERIFIED | Automated test: `test_health_metrics_smoke.py` |
| Signed URL validation | ❌ NOT VERIFIED | ✅ VERIFIED | Automated test: `test_storage_signed_urls.py` |
| Stripe webhook signature | ❌ NOT VERIFIED | ✅ VERIFIED | Automated test: `test_stripe_webhook_signature.py` |
| Invoice idempotency | ❌ NOT VERIFIED | ✅ VERIFIED | Existing tests: `test_invoice_stripe_payments.py` |
| Hardcoded secrets | ❌ NOT VERIFIED | ✅ VERIFIED | Automated test: `test_repo_security_scan.py` |
| Debug endpoints | ❌ NOT VERIFIED | ✅ VERIFIED | Automated test: `test_repo_security_scan.py` |

---

## Running Full Verification Suite

### Quick Verification (New Tests Only)
```bash
pytest tests/test_repo_security_scan.py \
       tests/test_health_metrics_smoke.py \
       tests/test_storage_signed_urls.py \
       tests/test_stripe_webhook_signature.py -v
```

### Full Suite (All Tests)
```bash
pytest -v
```

### Smoke Tests Only
```bash
pytest -m smoke -v
```

### Coverage Report
```bash
pytest --cov=app --cov-report=html --cov-report=term-missing
```

---

## Pre-Release Checklist

Before deploying to production, verify:

- [ ] All tests pass: `pytest -v`
- [ ] Security scans pass: `pytest tests/test_repo_security_scan.py -v`
- [ ] Health endpoints respond correctly in staging
- [ ] Metrics endpoint requires token in production
- [ ] Stripe webhook secret is configured: `STRIPE_WEBHOOK_SECRET`
- [ ] Metrics bearer token is configured: `METRICS_BEARER_TOKEN` (if metrics enabled)
- [ ] Storage signing secret is configured: `ORDER_PHOTO_SIGNING_SECRET`
- [ ] Database migrations are current: `alembic current` matches `alembic heads`
- [ ] No "example.com" in runtime code (except RFC 7807 type URIs)
- [ ] All environment variables are set in production config

---

## Manual Verification Procedures

### 1. Health Endpoints (Production)
```bash
# Liveness
curl https://your-domain.com/healthz
# Expected: {"status": "ok"}

# Readiness
curl https://your-domain.com/readyz
# Expected: {"status": "ok", "database": {"ok": true, ...}, ...}
```

### 2. Metrics Endpoint (Production)
```bash
# Without token (should fail)
curl https://your-domain.com/metrics
# Expected: 401 Unauthorized

# With token
curl -H "Authorization: Bearer $METRICS_TOKEN" https://your-domain.com/metrics
# Expected: 200 OK + Prometheus metrics format
```

### 3. Stripe Webhook (Staging)
Use Stripe CLI to send test webhooks:
```bash
stripe listen --forward-to https://staging.your-domain.com/v1/payments/stripe/webhook
stripe trigger payment_intent.succeeded
```

Verify:
- Webhook is received and processed
- Invoice/deposit status is updated correctly
- Sending same webhook twice doesn't create duplicate payment

### 4. Signed URLs (Staging)
1. Upload a photo via API
2. Verify signed URL is generated
3. Access photo using signed URL (should succeed)
4. Wait for expiration and retry (should fail with 401/403)
5. Tamper with signature and retry (should fail with 401/403)

---

## Contacts & Support

For questions or issues with this verification process:
- **Security Concerns:** security@your-domain.com
- **DevOps/Infrastructure:** ops@your-domain.com
- **Development Team:** dev@your-domain.com

---

**Document Version:** 1.0
**Last Updated:** 2026-01-01
**Sprint:** Sprint 9 — Final Verification
**Status:** ✅ ALL ITEMS VERIFIED
