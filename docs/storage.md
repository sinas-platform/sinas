---
title: "Storage"
---

## Storage

### Collections & Files

Collections are containers for file uploads with versioning, metadata validation, and processing hooks.

**Collection properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `metadata_schema` | JSON Schema that file metadata must conform to |
| `content_filter_function` | Function that runs on upload to approve/reject files |
| `post_upload_function` | Function that runs after upload for processing |
| `max_file_size_mb` | Per-file size limit (default: 100 MB) |
| `max_total_size_gb` | Total collection size limit (default: 10 GB) |

**File features:**

- **Versioning** — Every upload creates a new version. Previous versions are preserved.
- **Metadata** — Each file carries JSON metadata validated against the collection's schema.
- **Visibility** — Files can be `private` (owner only) or `shared` (users with collection `:all` access).
- **Content filtering** — Optional function runs on upload that can approve, reject, or modify the file.

**Management endpoints:**

```
POST   /api/v1/collections                              # Create collection
GET    /api/v1/collections                              # List collections
GET    /api/v1/collections/{namespace}/{name}           # Get collection
PUT    /api/v1/collections/{namespace}/{name}           # Update
DELETE /api/v1/collections/{namespace}/{name}           # Delete (cascades to files)
```

**Runtime file endpoints:**

```
POST   /files/{namespace}/{collection}                   # Upload file
GET    /files/{namespace}/{collection}                   # List files
GET    /files/{namespace}/{collection}/{filename}        # Download file
PATCH  /files/{namespace}/{collection}/{filename}        # Update metadata
DELETE /files/{namespace}/{collection}/{filename}        # Delete file
POST   /files/{namespace}/{collection}/{filename}/url    # Generate temporary download URL
POST   /files/{namespace}/{collection}/search            # Search files
```

### States

States are a persistent key-value store organized by namespace. Agents use states to maintain memory and context across conversations.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` | Organizational grouping (e.g., `preferences`, `memory`, `api_keys`) |
| `key` | Unique key within user + namespace |
| `value` | Any JSON data |
| `visibility` | `private` (owner only) or `shared` (users with namespace `:all` permission) |
| `description` | Optional description |
| `tags` | Tags for filtering and search |
| `relevance_score` | Priority for context retrieval (0.0–1.0, default: 1.0) |
| `encrypted` | If `true`, value is encrypted at rest with Fernet and decrypted on read |
| `expires_at` | Optional expiration time |

**Encrypted states:** Set `encrypted: true` when creating or updating a state to store the value encrypted. The plaintext value is stored in `encrypted_value` (Fernet-encrypted) while `value` is set to `{}`. On read, the value is transparently decrypted. This is useful for storing API keys, tokens, or other secrets.

**Agent state access** is declared per agent via `enabled_stores`:

```yaml
enabledStores:
  - store: "shared_knowledge/main"
    access: readonly
  - store: "conversation_memory/main"
    access: readwrite
```

Read-only stores give the agent a `retrieve_context` tool. Read-write stores additionally provide `save_context`, `update_context`, and `delete_context`.

**Endpoints:**

```
POST   /states              # Create state entry
GET    /states              # List (supports namespace, visibility, tags, search filters)
GET    /states/{id}         # Get state
PUT    /states/{id}         # Update state
DELETE /states/{id}         # Delete state
```

---
