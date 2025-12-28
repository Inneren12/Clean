# Invoices

## Numbering
- Format: `INV-YYYY-######`
- Numbers are allocated atomically in the database to avoid duplicates during concurrent creation.

## Statuses
- `DRAFT`: newly created invoice (default)
- `SENT`: delivered to customer (email/PDF out of scope here)
- `PARTIAL`: some manual payments received but balance remains
- `PAID`: balance cleared
- `OVERDUE`: due date passed without full payment
- `VOID`: cancelled/invalid invoice
- Only the above values are accepted. Invalid statuses are rejected with HTTP 422.

## Creating invoices
- Endpoint: `POST /v1/admin/orders/{order_id}/invoice`
- Body: `issue_date` (optional), `due_date` (optional), `currency`, `notes`, `items[]` (qty, description, unit_price_cents, optional tax_rate)
- Currency defaults to CAD. At least one item is required; totals are calculated server-side.

## Listing and viewing
- `GET /v1/admin/invoices` supports filters: `status`, `customer_id`, `order_id`, `q` (invoice number search), and `page`.
- `GET /v1/admin/invoices/{invoice_id}` returns full items + payment history with balances.
- Public links: invoices can be viewed without authentication via `/i/{token}`. Tokens are long, random, and only hashes are stored in the database. Rotating a token invalidates older links. PDF downloads use `/i/{token}.pdf`.

## Manual payments
- Endpoint: `POST /v1/admin/invoices/{invoice_id}/record-payment` (preferred)
- Legacy endpoint (still supported): `POST /v1/admin/invoices/{invoice_id}/mark-paid`
- Body: `amount_cents`, `method` (`cash`, `etransfer`, `other`), optional `reference` and `received_at`.
- Creates a manual payment record and updates invoice status to `PARTIAL` or `PAID` based on remaining balance.
- Invoices record the admin username in `created_by` when they are created; manual payments do not change `created_by`.

## Sending invoices
- Endpoint: `POST /v1/admin/invoices/{invoice_id}/send`
- Generates/rotates a long URL-safe public token and emails the customer a link to `/i/{token}` (PDF link `/i/{token}.pdf`).
- The database stores only an HMAC-SHA256 hash of the token, keyed by a secret (`INVOICE_PUBLIC_TOKEN_SECRET`); the raw token only appears in the emailed URL/response.
- If the invoice is still `DRAFT`, sending transitions it to `SENT`.
- Token metadata tracks the last send time; rotate by re-sending.
- Public links intentionally avoid exposing customer or invoice IDs; do not include PII in URLs.

## Public access endpoints
- `GET /i/{token}` renders a minimal HTML invoice view and includes a “Download PDF” link.
- `GET /i/{token}.pdf` returns a lightweight PDF (latin-1 text only; non-Latin characters may be replaced).
- `PUBLIC_BASE_URL` can be set to ensure links in emails work correctly behind proxies/CDNs.
