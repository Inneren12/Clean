# Cloudflare Scheduler templates

Use these examples with Cloudflare Workers + Scheduler triggers. Each job posts to the same admin endpoints listed in `OPERATIONS.md`. Replace `API_BASE` and secrets before deploying.

```jsonc
// Email inbound scan every 5 minutes
{
  "name": "email-scan",
  "cron": "*/5 * * * *",
  "endpoint": "${API_BASE}/v1/admin/email-scan",
  "request": {
    "method": "POST",
    "headers": {
      "Authorization": "Basic ${b64encode(ADMIN_BASIC_USERNAME:ADMIN_BASIC_PASSWORD)}"
    }
  }
}
```

```jsonc
// Daily cleanup at 04:00 UTC
{
  "name": "cleanup",
  "cron": "0 4 * * *",
  "endpoint": "${API_BASE}/v1/admin/cleanup",
  "request": {
    "method": "POST",
    "headers": {
      "Authorization": "Basic ${b64encode(ADMIN_BASIC_USERNAME:ADMIN_BASIC_PASSWORD)}"
    }
  }
}
```

```jsonc
// Retention cleanup at 02:00 UTC
{
  "name": "retention-cleanup",
  "cron": "0 2 * * *",
  "endpoint": "${API_BASE}/v1/admin/retention/cleanup",
  "request": {
    "method": "POST",
    "headers": {
      "Authorization": "Basic ${b64encode(ADMIN_BASIC_USERNAME:ADMIN_BASIC_PASSWORD)}"
    }
  }
}
```

```jsonc
// Export DLQ replay every 15 minutes
{
  "name": "export-dlq",
  "cron": "*/15 * * * *",
  "endpoint": "${API_BASE}/v1/admin/export-dead-letter",
  "request": {
    "method": "POST",
    "headers": {
      "Authorization": "Basic ${b64encode(ADMIN_BASIC_USERNAME:ADMIN_BASIC_PASSWORD)}"
    }
  }
}
```

```jsonc
// Outbox DLQ replay every 15 minutes
{
  "name": "outbox-dlq",
  "cron": "*/15 * * * *",
  "endpoint": "${API_BASE}/v1/admin/outbox/dead-letter",
  "request": {
    "method": "POST",
    "headers": {
      "Authorization": "Basic ${b64encode(ADMIN_BASIC_USERNAME:ADMIN_BASIC_PASSWORD)}"
    }
  }
}
```

For optional runner-based jobs, deploy a Worker that invokes `python -m app.jobs.run ...` inside your container or VM on a fixed schedule (for example, a long-running container with Cloudflare Scheduler hitting a management endpoint). Keep the same frequencies as the cron example.
