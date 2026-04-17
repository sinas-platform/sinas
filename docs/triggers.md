---
title: "Triggers"
---

## Triggers

### Webhooks

Webhooks expose functions as HTTP endpoints. When a request arrives at a webhook path, Sinas executes the linked function with the request data.

**Key properties:**

| Property | Description |
|---|---|
| `path` | URL path (e.g., `stripe/payment-webhook`) |
| `http_method` | GET, POST, PUT, DELETE, or PATCH |
| `function_namespace` / `function_name` | Target function |
| `requires_auth` | Whether the caller must provide a Bearer token |
| `default_values` | Default parameters merged with request data (request takes priority) |

**How input is extracted:**

- `POST`/`PUT`/`PATCH` with JSON body → body becomes the input
- `GET` → query parameters become the input
- Default values are merged underneath (request data overrides)

**Example:**

```bash
# Create a webhook
curl -X POST https://yourdomain.com/api/v1/webhooks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "stripe/payment",
    "function_namespace": "payments",
    "function_name": "process_webhook",
    "http_method": "POST",
    "requires_auth": false,
    "default_values": {"source": "stripe"}
  }'

# Trigger it
curl -X POST https://yourdomain.com/webhooks/stripe/payment \
  -H "Content-Type: application/json" \
  -d '{"event": "charge.succeeded", "amount": 1000}'

# Function receives: {"source": "stripe", "event": "charge.succeeded", "amount": 1000}
```

**Endpoints:**

```
POST   /api/v1/webhooks              # Create webhook
GET    /api/v1/webhooks              # List webhooks
GET    /api/v1/webhooks/{path}       # Get webhook
PATCH  /api/v1/webhooks/{path}       # Update webhook
DELETE /api/v1/webhooks/{path}       # Delete webhook
```

### Schedules

Schedules trigger functions or agents on a cron timer.

**Key properties:**

| Property | Description |
|---|---|
| `name` | Unique name (per user) |
| `schedule_type` | `function` or `agent` |
| `target_namespace` / `target_name` | Function or agent to trigger |
| `cron_expression` | Standard cron expression (e.g., `0 9 * * MON-FRI`) |
| `timezone` | Schedule timezone (default: `UTC`) |
| `input_data` | Input passed to the function or agent |
| `content` | Message content (agent schedules only) |

For agent schedules, a new chat is created for each run with the schedule name and timestamp as the title.

**Endpoints:**

```
POST   /api/v1/schedules              # Create schedule
GET    /api/v1/schedules              # List schedules
GET    /api/v1/schedules/{name}       # Get schedule
PATCH  /api/v1/schedules/{name}       # Update schedule
DELETE /api/v1/schedules/{name}       # Delete schedule
```

### Database Triggers (CDC)

Database triggers watch external database tables for changes and automatically execute functions when new or updated rows are detected. This is poll-based Change Data Capture — no setup required on the source database.

**How it works:**

1. A separate CDC service polls the configured table at a fixed interval
2. It queries rows where `poll_column > last_bookmark` (e.g., `updated_at > '2026-03-01T10:00:00'`)
3. If new rows are found, they are batched into a single function call
4. The bookmark advances to the highest value in the batch
5. On first activation, the bookmark is set to `MAX(poll_column)` — no backfill of existing data

**Key properties:**

| Property | Description |
|---|---|
| `name` | Unique trigger name (per user) |
| `database_connection_id` | Which database connection to poll |
| `schema_name` | Database schema (default: `public`) |
| `table_name` | Table to watch |
| `operations` | `["INSERT"]`, `["UPDATE"]`, or both |
| `function_namespace` / `function_name` | Function to execute when changes are detected |
| `poll_column` | Monotonically increasing column used as bookmark (e.g., `updated_at`, `id`) |
| `poll_interval_seconds` | How often to poll (1–3600, default: `10`) |
| `batch_size` | Max rows per poll (1–10000, default: `100`) |
| `is_active` | Enable/disable without deleting |
| `last_poll_value` | Current bookmark (managed automatically) |
| `error_message` | Last error, if any (visible in UI) |

The `poll_column` must be a column whose value only increases — timestamps, auto-increment IDs, or sequences. The column type is detected automatically and comparisons are cast to the correct type.

**Function input payload:**

When changes are detected, the target function receives all new rows in a single call:

```json
{
  "table": "public.orders",
  "operation": "CHANGE",
  "rows": [
    {"id": 123, "status": "paid", "amount": 99.50, "updated_at": "2026-03-02T10:30:00Z"},
    {"id": 124, "status": "pending", "amount": 45.00, "updated_at": "2026-03-02T10:30:01Z"}
  ],
  "poll_column": "updated_at",
  "count": 2,
  "timestamp": "2026-03-02T10:30:05Z"
}
```

If a poll returns zero rows, no function call is made.

**Error handling:** On failure, the trigger logs the error to `error_message` and retries with exponential backoff (up to 60 seconds). The trigger continues retrying until deactivated or the issue is resolved.

**Limitations:**
- Cannot detect `DELETE` operations (poll-based limitation)
- Changes are detected with a delay equal to the poll interval
- The `poll_column` must never decrease — resetting it will cause missed or duplicate rows

**Endpoints:**

```
POST   /api/v1/database-triggers              # Create trigger
GET    /api/v1/database-triggers              # List triggers
GET    /api/v1/database-triggers/{name}       # Get trigger
PATCH  /api/v1/database-triggers/{name}       # Update trigger
DELETE /api/v1/database-triggers/{name}       # Delete trigger
```

**Declarative configuration:**

```yaml
databaseTriggers:
  - name: "customer_changes"
    connectionName: "prod_database"
    tableName: "customers"
    operations: ["INSERT", "UPDATE"]
    functionName: "sync/process_customer"
    pollColumn: "updated_at"
    pollIntervalSeconds: 10
    batchSize: 100
```

---
