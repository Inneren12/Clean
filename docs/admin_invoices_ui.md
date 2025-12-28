# Admin invoices UI

The invoices admin UI is server-rendered inside `app/api/routes_admin.py` alongside the existing observability page.

## Routes
- `GET /v1/admin/ui/invoices` — list with filters for status, customer_id, order_id, invoice number (q), and pagination.
- `GET /v1/admin/ui/invoices/{invoice_id}` — detail view showing items, payments, balances, and actions.

Both routes require admin Basic Auth (`ADMIN_BASIC_USERNAME` / `ADMIN_BASIC_PASSWORD`). Links from the observability nav point to the invoices tab.

## Actions
- **Send invoice**: calls `POST /v1/admin/invoices/{invoice_id}/send`, updates status to `SENT` when applicable, and surfaces the public link for quick copy.
- **Record manual payment**: submits to `POST /v1/admin/invoices/{invoice_id}/record-payment` with amount, method, and reference. The UI appends the new payment row and refreshes totals/balance.

## Notes
- Status badges and overdue balance styling match the lightweight admin CSS already used in observability.
- Copy buttons exist for invoice ID/number and the generated public link (after sending).
