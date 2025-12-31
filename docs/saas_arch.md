# SaaS Architecture Overview (Sprint 1)

## Tenancy model
- Organizations are first-class tenants (`organizations` table). All SaaS users belong to at least one organization through `memberships`.
- Users are global and identified by email (unique across all tenants) to keep credential reuse predictable during migration.
- Memberships include the tenant role and activation flag; tenants can disable users without deleting data.
- Service-to-service access uses API tokens that are scoped to an organization and a membership role.
- Runtime request context stores `request.state.current_org_id` and `request.state.saas_identity` when a SaaS session token is present.

## Authentication & session flow
- Users authenticate via `/v1/auth/login` with email/password (hashed using a salted SHA256 helper). A JWT access token is issued containing `sub` (user_id), `org_id`, and `role`.
- The `TenantSessionMiddleware` reads `Authorization: Bearer` or the `saas_session` cookie, validates the token, and loads the active membership. On success it attaches the identity to the request state for downstream dependencies.
- Legacy BasicAuth remains available behind `LEGACY_BASIC_AUTH_ENABLED` for Admin/Worker portals to preserve current flows during transition.

## RBAC
- Membership roles map to existing admin permissions: owner/admin → full access, dispatcher → dispatch/view, finance → finance/view, viewer → read-only.
- Dependencies such as `require_permissions` and `require_finance` evaluate the mapped admin permissions, so existing admin routes inherit the SaaS role checks.
- Tenant-aware routes (e.g., org member listing) also compare the token’s `org_id` with the path parameter to prevent cross-tenant access.

## Migration strategy
- Alembic revision `0031_saas_multitenant_auth` introduces the new tables and seeds a default organization for development/testing.
- Tests rely on the seeded default organization; production data should create initial org/users via an explicit bootstrap script or admin flow.
- Legacy BasicAuth can be disabled by setting `LEGACY_BASIC_AUTH_ENABLED=false` once SaaS auth is configured.
