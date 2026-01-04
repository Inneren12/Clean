# Performance notes (P1 hotspots)

## What changed
- Added SQL-level pagination for finance reconciliation and export dead-letter listings to avoid loading entire datasets into application memory.
- Introduced total counters in responses to keep pagination UX intact while remaining backward compatible with item payloads.
- Added composite indexes to accelerate common queue filters and ordering:
  - `order_photos(org_id, review_status, created_at)` and `order_photos(org_id, needs_retake, created_at)` for photo review queues.
  - `bookings(org_id, assigned_worker_id, status, starts_at)` for unassigned booking lookups.
  - `export_events(org_id, last_error_code, created_at)` for export dead-letter scans.

## Why it helps
- Heavy queue/timeline consumers now rely on database pagination, reducing response latency and memory pressure for large tenants.
- Covering indexes align with the hottest WHERE/ORDER BY clauses, improving row lookup and sorting efficiency without altering RBAC or org scoping semantics.
