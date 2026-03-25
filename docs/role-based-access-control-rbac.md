## Role-Based Access Control (RBAC)

### Overview

Users are assigned to **roles**, and roles define **permissions**. A user's effective permissions are the union of all permissions from all their roles (OR logic). Permissions are loaded from the database on every request — changes take effect immediately.

### Default Roles

| Role | Description |
|---|---|
| **Admins** | Full access to everything (`sinas.*:all`) |
| **Users** | Create and manage own resources, chat with any agent, execute own functions |
| **GuestUsers** | Read and update own profile only |

### Permission Format

```
<service>.<resource>[/<path>].<action>:<scope>
```

**Components:**

| Part | Description | Examples |
|---|---|---|
| **Service** | Top-level namespace | `sinas`, or a custom prefix like `titan`, `acme` |
| **Resource** | Resource type | `agents`, `functions`, `states`, `users` |
| **Path** | Optional namespace/name path | `/marketing/send_email`, `/*` |
| **Action** | What operation is allowed | `create`, `read`, `update`, `delete`, `execute`, `chat` |
| **Scope** | Ownership scope | `:own` (user's resources), `:all` (all resources) |

### Permission Matching Rules

**Scope hierarchy:** `:all` automatically grants `:own`. A user with `sinas.agents.read:all` passes any check for `sinas.agents.read:own`.

**Wildcards** can be used at any level:

| Pattern | Matches |
|---|---|
| `sinas.*:all` | Everything in Sinas (admin access) |
| `sinas.agents/*/*.chat:all` | Chat with any agent in any namespace |
| `sinas.functions/marketing/*.execute:own` | Execute any function in the `marketing` namespace |
| `sinas.states/*.read:own` | Read own states in any namespace |
| `sinas.chats.*:own` | All chat actions (read, update, delete) on own chats |

**Namespaced resource permissions** use slashes in the resource path:

```
sinas.agents/support/ticket-bot.chat:own        # Chat with specific agent
sinas.functions/*/send_email.execute:own         # Execute send_email in any namespace
sinas.states/api_keys.read:all                   # Read all shared states in api_keys namespace
```

**Non-namespaced resource permissions** use simple dot notation:

```
sinas.webhooks.create:own                        # Create webhooks
sinas.schedules.read:own                         # Read own schedules
sinas.users.update:own                           # Update own profile
```

### Custom Permissions

The permission system is not limited to `sinas.*`. You can define permissions with any service prefix for your own applications:

```
titan.student_profile.read:own
titan.courses/math/*.enroll:own
acme.billing.invoices.read:all
myapp.*:all
```

These work identically to built-in permissions — same wildcard matching, same scope hierarchy. This lets you use Sinas as the authorization backend for external applications.

### Checking Permissions from External Services

Use the `POST /auth/check-permissions` endpoint to verify whether the current user (identified by their Bearer token or API key) has specific permissions:

```bash
curl -X POST https://yourdomain.com/auth/check-permissions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "permissions": ["titan.student_profile.read:own", "titan.courses.enroll:own"],
    "logic": "AND"
  }'
```

Response:

```json
{
  "result": true,
  "logic": "AND",
  "checks": [
    {"permission": "titan.student_profile.read:own", "has_permission": true},
    {"permission": "titan.courses.enroll:own", "has_permission": true}
  ]
}
```

- **`logic: "AND"`** — User must have ALL listed permissions (default)
- **`logic: "OR"`** — User must have AT LEAST ONE of the listed permissions

This makes Sinas usable as a centralized authorization service for any number of external applications.

### Managing Roles

```
POST   /api/v1/roles                        # Create role
GET    /api/v1/roles                        # List roles
GET    /api/v1/roles/{name}                 # Get role details
PATCH  /api/v1/roles/{name}                 # Update role
DELETE /api/v1/roles/{name}                 # Delete role
POST   /api/v1/roles/{name}/members         # Add user to role
DELETE /api/v1/roles/{name}/members/{id}    # Remove user from role
POST   /api/v1/roles/{name}/permissions     # Set permission
DELETE /api/v1/roles/{name}/permissions     # Remove permission
GET    /api/v1/permissions/reference        # List all known permissions
```

---
