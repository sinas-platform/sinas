import json
import os
from typing import Optional

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings

VALID_AUTH_MODES = {"otp", "password", "password+otp"}


class Settings(BaseSettings):
    # Database - can be set as full URL or individual components
    database_url: Optional[str] = None
    database_user: str = "postgres"
    database_password: str = "password"
    database_host: str = "localhost"
    database_port: str = "5432"
    database_name: str = "sinas"

    # Direct postgres connection (bypasses pgbouncer, used for migrations)
    database_direct_host: Optional[str] = None

    @property
    def get_database_url(self) -> str:
        """Build database URL from components if not explicitly set."""
        if self.database_url:
            return self.database_url
        return f"postgresql://{self.database_user}:{self.database_password}@{self.database_host}:{self.database_port}/{self.database_name}"

    @property
    def get_database_direct_url(self) -> str:
        """Database URL that bypasses pgbouncer (for migrations/DDL)."""
        host = self.database_direct_host or self.database_host
        return f"postgresql://{self.database_user}:{self.database_password}@{host}:{self.database_port}/{self.database_name}"

    # ClickHouse
    clickhouse_host: str = os.getenv("CLICKHOUSE_HOST", "localhost")
    clickhouse_port: int = int(os.getenv("CLICKHOUSE_PORT", "8123"))  # HTTP port
    clickhouse_user: str = os.getenv("CLICKHOUSE_USER", "default")
    clickhouse_password: str = os.getenv("CLICKHOUSE_PASSWORD", "")
    clickhouse_database: str = os.getenv("CLICKHOUSE_DATABASE", "sinas")
    clickhouse_retention_days: int = int(os.getenv("CLICKHOUSE_RETENTION_DAYS", "90"))
    clickhouse_hot_retention_days: int = int(os.getenv("CLICKHOUSE_HOT_RETENTION_DAYS", "30"))

    # Application
    debug: bool = False
    secret_key: str = "your-secret-key-change-in-production"
    # Deprecated: superseded by jwt_algorithm below. Kept so existing
    # ALGORITHM=HS256 env entries don't fail settings validation; internal
    # purpose tokens (file serve, component render) are pinned to HS256.
    algorithm: str = "HS256"
    # Access-token signing (#101). Default HS256 keeps tokens verifiable
    # exactly as before (shared secret_key). RS256 signs with an RSA keypair
    # so external services can verify Sinas tokens offline with standard JWT
    # middleware via GET /.well-known/jwks.json.
    jwt_algorithm: str = "HS256"  # HS256 | RS256
    # RS256 access tokens carry iss + aud claims. Issuer defaults to
    # public_base_url (see token_issuer property).
    jwt_issuer: str = ""
    jwt_audience: str = "sinas"
    # RS256 private key resolution order: JWT_PRIVATE_KEY (PEM content) →
    # JWT_PRIVATE_KEY_FILE (path) → auto-generated and persisted encrypted in
    # the database (shared by all processes).
    jwt_private_key: str = ""
    jwt_private_key_file: str = ""
    uvicorn_workers: int = 4  # Number of Uvicorn worker processes
    # JWT Token Configuration (Best Practice)
    access_token_expire_minutes: int = 15  # Short-lived access tokens
    refresh_token_expire_days: int = 30  # Long-lived refresh tokens

    # Per-execution token TTL — the in-context access_token handed to functions
    # is minted with `expires_delta = min(function.timeout + buffer, max)` so it
    # outlives the execution but doesn't outlive it by much.
    execution_token_buffer_seconds: int = 300  # 5 min headroom after function returns
    max_execution_token_seconds: int = 86400  # 24 h hard cap

    # Callback URL allowlist for /functions/{ns}/{name}/execute/async.
    # - Unset / empty: callback feature disabled (request with callback_url → 400)
    # - "*": permissive — any HTTPS non-private URL accepted
    # - Comma-separated host list: exact-host allowlist (e.g. "app1.example.com,app2.example.com")
    callback_url_hosts: Optional[str] = None

    # Cap on batch-submission size (function and agent batches). Prevents
    # accidental DoS-by-batch via the bulk-enqueue endpoints.
    max_batch_size: int = 1000

    # OTP Configuration
    otp_expire_minutes: int = 10
    otp_max_attempts: int = 2  # Max verification attempts before OTP is invalidated

    # Rate limiting
    rate_limit_login_ip_max: int = 10  # Max login requests per IP per window
    rate_limit_login_email_max: int = 5  # Max login requests per email per window
    rate_limit_otp_ip_max: int = 10  # Max OTP verify requests per IP per window
    rate_limit_setup_link_ip_max: int = 10  # Max setup-link exchange/complete requests per IP per window
    rate_limit_window_seconds: int = 900  # Rate limit window (15 minutes)

    # SMTP Configuration (for sending emails)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_domain: Optional[str] = None  # Used for "from" email: login@{smtp_domain}

    # SMTP Server Configuration (for receiving emails)
    smtp_server_host: str = "0.0.0.0"
    smtp_server_port: int = 2525  # Port for incoming email SMTP server

    # Executor selection — see `app/services/executor/` for the abstraction.
    # - sandbox_executor: backend for untrusted code (per-function untrusted
    #   functions and agent codeExecution). Must isolate per execution.
    #     "docker_pool"      — long-lived Docker container pool (current default)
    #     "docker_ephemeral" — single-use Docker container per execution
    #     "k8s_pod"          — single-use k8s Pod per execution (for k8s deploys)
    #     "disabled"         — sandbox features rejected; deploy is trusted-only
    # - trusted_executor: backend for admin-approved code (Function.shared_pool=True).
    #     "docker_shared" — dedicated long-lived Docker workers (current default)
    #     "k8s_shared"    — dedicated long-lived k8s pods (for k8s deploys;
    #                       credential-free, meter-integrity safe)
    #     "inprocess"     — run inside the calling process; no Docker socket
    #                       needed. NOT meter-integrity safe: trusted code runs
    #                       in a credential-bearing process.
    #     "disabled"      — shared_pool executions rejected with a clear error
    sandbox_executor: str = "docker_pool"
    trusted_executor: str = "docker_shared"

    # k8s_pod sandbox executor (only read when sandbox_executor="k8s_pod").
    # Runs in-cluster: credentials come from the pod's ServiceAccount, which
    # needs create/get/delete + exec on pods in `k8s_sandbox_namespace`.
    k8s_sandbox_namespace: str = ""  # "" = this pod's namespace (POD_NAMESPACE env or serviceaccount file)
    k8s_sandbox_image: str = ""  # "" = function_container_image; must be pullable by the cluster
    k8s_sandbox_service_account: str = ""  # SA for sandbox pods; "" = namespace default
    k8s_sandbox_pod_ready_timeout: int = 120  # seconds to wait for a sandbox pod to become Ready
    k8s_sandbox_install_dependencies: bool = True  # pip install Dependency specs into each pod (skip if baked into k8s_sandbox_image)

    # Scheduling for sandbox pods — deliberately dumb/generic so the actual
    # policy (spread one-client-per-node vs. pack many clients per node,
    # etc.) lives entirely in the Helm chart's values, not in app code. This
    # app only applies whatever it's handed:
    #   - k8s_release_name: stamped as app.kubernetes.io/instance on the pod,
    #     so chart-authored affinity rules (either direction) have something
    #     to match on. "" = no label.
    #   - k8s_sandbox_node_selector / k8s_sandbox_tolerations: verbatim
    #     nodeSelector / tolerations, JSON-encoded.
    #   - k8s_sandbox_affinity: a verbatim K8s `affinity` object (podAffinity
    #     to pack onto the same node as other pods matching a label,
    #     podAntiAffinity to spread, nodeAffinity, or any combination),
    #     JSON-encoded. The chart decides which shape to send — e.g. required
    #     podAntiAffinity for a paid/isolated plan, required podAffinity
    #     keyed on a shared "plan=free" label to force free-tier clients onto
    #     the same node, or "{}" for no constraint. Changing that policy is a
    #     chart values change, not an app change.
    # All empty/no-op by default — matches today's behavior on generic
    # clusters with no per-client scheduling policy.
    k8s_release_name: str = ""
    # k8s_shared trusted executor: number of warm trusted worker pods.
    k8s_trusted_workers: int = 2
    k8s_sandbox_node_selector: str = "{}"  # JSON object, e.g. {"role": "shared"}
    k8s_sandbox_tolerations: str = "[]"  # JSON list of Toleration dicts
    k8s_sandbox_affinity: str = "{}"  # JSON k8s Affinity object (podAffinity/podAntiAffinity/nodeAffinity)

    @field_validator("k8s_sandbox_node_selector", "k8s_sandbox_affinity")
    @classmethod
    def _validate_k8s_json_object(cls, v: str, info: ValidationInfo) -> str:
        # Fail at startup, not on the first sandbox execution: a bad value
        # here would otherwise surface as an unhandled JSONDecodeError deep
        # inside pod creation instead of a clear config error.
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError(f"{info.field_name} must be valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise ValueError(
                f"{info.field_name} must be a JSON object, got {type(parsed).__name__}"
            )
        return v

    @field_validator("k8s_sandbox_tolerations")
    @classmethod
    def _validate_k8s_tolerations(cls, v: str) -> str:
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError(f"k8s_sandbox_tolerations must be valid JSON: {e}") from e
        if not isinstance(parsed, list):
            raise ValueError(
                f"k8s_sandbox_tolerations must be a JSON array, got {type(parsed).__name__}"
            )
        return v

    # Function execution (always uses Docker for isolation)
    function_timeout: int = 300  # 5 minutes (max execution time)
    # Max nesting depth for execution chains (a function/agent invoking another
    # execution). A nested call exceeding this is rejected fast instead of
    # silently exhausting the shared worker pool and deadlocking. Defaults to
    # `default_worker_count` (None = auto) so a full chain always fits the pool
    # (depth <= workers); set an explicit value to override, or 0 to disable.
    max_execution_depth: Optional[int] = None
    # Reserve N shared-worker slots that only NESTED executions (depth > 0) may
    # use, so a parent blocked on a child never starves the pool (the nested-
    # call deadlock). 0 = disabled (no admission control; default — no behaviour
    # change). For full single-chain safety set this to max_execution_depth - 1
    # AND keep default_worker_count >= max_execution_depth. A smaller value still
    # breaks the common contention case while throttling top-level work less.
    shared_pool_reserve: int = 0
    max_function_memory: int = 512  # MB (Docker memory limit)
    max_function_cpu: float = 1.0  # CPU cores (1.0 = 1 full core, 0.5 = half core)
    max_function_storage: str = "1g"  # Disk storage limit (e.g., "500m", "1g")
    function_container_image: str = "sinas-executor"  # Base image for execution (overridden by FUNCTION_CONTAINER_IMAGE env var)
    function_container_idle_timeout: int = 3600  # Seconds before idle container cleanup (1 hour)

    # Sandbox containers (isolated execution pool)
    sandbox_min_size: int = 4  # Containers to create on startup
    sandbox_max_size: int = 20  # Maximum sandbox containers
    sandbox_min_idle: int = 2  # Trigger replenish when idle drops below this
    sandbox_max_executions: int = 100  # Recycle container after this many executions
    sandbox_acquire_timeout: int = 30  # Seconds to wait for a container

    # Optional platform features. Both default to on (no change for existing
    # deployments); turning them off removes capability rather than hiding it,
    # so the affected endpoints reject explicitly instead of failing oddly.
    #
    # code_execution_enabled=False disables ALL execution of user-supplied code:
    # Functions and the agent `codeExecution` tool. Pair it with
    # SANDBOX_EXECUTOR=disabled for the lightest deployment — no sandbox pool,
    # no per-execution pods, no executor image needed.
    code_execution_enabled: bool = True
    # builtin_database_enabled=False skips creating the `sinas_data` database
    # and its default DatabaseConnection record on startup. This is about not
    # provisioning a data store the operator never asked for (they bring their
    # own connections); it does NOT affect the platform's own Postgres, and an
    # already-created record is left alone.
    builtin_database_enabled: bool = True
    # cdc_enabled=False turns off Database Triggers (CDC): the poller doesn't
    # run and the API refuses to create a trigger or switch one on. Stored
    # triggers are left as they are, so turning CDC back on resumes exactly
    # the ones that were on. In Kubernetes, pair it with features.cdc=false,
    # which also drops the cdc-worker Deployment.
    cdc_enabled: bool = True

    # Package management
    allow_package_installation: bool = True
    allowed_packages: Optional[str] = None  # Comma-separated whitelist, None = all allowed

    # Database pool
    db_pool_size: int = 20  # Connection pool size
    db_max_overflow: int = 30  # Max overflow connections beyond pool_size

    # Docker configuration
    backend_port: int = 8000  # Port the backend listens on (for file URLs on localhost)
    docker_network: str = "auto"  # Docker network for containers (auto-detect or specify)
    sandbox_network: str = "sinas-sandbox"  # Isolated network for executor containers (internet only, no access to internal services)
    default_worker_count: int = 4  # Number of workers to start on backend startup
    # Memory limit per shared worker container (Docker size string). Was
    # hardcoded to 1g; heavier post-processing functions (document parsing,
    # embedding prep) legitimately need more.
    worker_memory_limit: str = "1g"

    # Message history
    max_history_messages: int = 100  # Max messages to load for conversation history
    # Compaction: when a conversation outgrows max_history_messages, the
    # dropped prefix is summarized (asynchronously, incrementally) and the
    # summary rides along as context instead of the history just falling off
    # a cliff. Disable to restore pure windowing.
    compaction_enabled: bool = True
    compaction_summary_max_tokens: int = 800
    max_tool_iterations: int = 25  # Max consecutive tool-call rounds before stopping

    # Tool result store
    tool_result_retention_days: int = int(os.getenv("TOOL_RESULT_RETENTION_DAYS", "30"))
    tool_result_max_inline: int = int(os.getenv("TOOL_RESULT_MAX_INLINE", "5"))  # Last N results kept inline
    tool_result_max_size: int = int(os.getenv("TOOL_RESULT_MAX_SIZE", "102400"))  # 100KB truncation limit

    # All-in-one ("lite") deployment. When enabled, the API process also runs
    # the queue workers, scheduler and CDC poller as asyncio tasks — one
    # container instead of five. The scheduler and CDC loops are singletons,
    # so this REQUIRES a single uvicorn worker (UVICORN_WORKERS=1) and a
    # single backend replica.
    all_in_one: bool = False
    # Serve the console SPA from console_dist_path at /ui, replacing the
    # separate console container. Independent of all_in_one; skipped with a
    # warning when the directory doesn't exist.
    serve_console: bool = False
    console_dist_path: str = "/app/console-dist"

    # Redis & Queue
    redis_url: str = "redis://redis:6379/0"
    queue_function_concurrency: int = 10
    queue_agent_concurrency: int = 5
    queue_agent_sub_concurrency: int = 5  # concurrency of the sub-agent queue worker
    queue_default_timeout: int = 300
    queue_max_retries: int = 3
    # Saturation backpressure: how long a function job may wait in the queue
    # (deferred retries, backoff capped at 30s) for a shared-pool slot before
    # it becomes a real failure. The queue IS the waiting room — bulk ingest
    # (thousands of uploads onto a small pool) is expected to drain over
    # hours. The bound exists only so a permanently wedged pool eventually
    # fails loudly instead of spinning forever. 0 = wait forever.
    queue_saturation_timeout_seconds: int = 21600  # 6 hours
    queue_retry_delay: int = 10
    # How often an idle worker checks its queue for new jobs (seconds). arq's
    # default, 0.5, added up to half a second before a queued job started: an
    # agent message on a keep-alive chat, and every function a tool call
    # runs. A check is one cheap Redis command.
    queue_poll_delay: float = 0.05

    # Pipeline runs (see ADR 2026-07-28-pipelines-triggers-and-linear-steps).
    # Runs are await-heavy orchestration → high concurrency is cheap.
    queue_pipeline_concurrency: int = 50
    pipeline_job_timeout: int = 1800  # hard ceiling for one queued run (incl. agent steps)
    pipeline_run_retention_days: int = 30  # pipeline_runs rows older than this are pruned

    # Chat retention (docs/design/chat-retention.md). Off unless enabled:
    # nothing is archived or deleted on a deployment that hasn't opted in.
    chat_retention_enabled: bool = False
    # Global default, in days since a chat's last activity; 0 = keep
    # forever. An agent's retentionDays overrides it (0 = keep forever).
    chat_retention_days: int = 0
    # archive: write each chat to the archive destination, then delete it
    # (no destination = refused, nothing deleted). delete: deleting without
    # an archive is allowed; a configured destination is still written first.
    chat_retention_action: str = "archive"
    chat_retention_schedule: str = "30 3 * * *"  # cron, UTC
    chat_retention_batch_size: int = 200
    chat_retention_max_chats_per_run: int = 0  # 0 = no limit; a capped run ends "partial"
    chat_retention_max_run_seconds: int = 3300  # a run past this ends "partial"; the next tick continues
    # Archive destination: any S3-compatible endpoint (AWS S3, GCS interop,
    # MinIO, R2). Sinas only ever calls PutObject. Credentials unset = the
    # standard AWS chain (env, web identity / IRSA, ECS / EKS pod identity,
    # instance metadata).
    chat_archive_s3_bucket: Optional[str] = None
    chat_archive_s3_prefix: str = "sinas/chats"
    chat_archive_s3_endpoint_url: Optional[str] = None  # unset = AWS S3
    chat_archive_s3_region: Optional[str] = None
    chat_archive_s3_access_key_id: Optional[str] = None
    chat_archive_s3_secret_access_key: Optional[str] = None
    chat_archive_s3_session_token: Optional[str] = None
    chat_archive_s3_kms_key_id: Optional[str] = None  # set = SSE-KMS with this key
    chat_archive_s3_addressing_style: str = "auto"  # auto | path | virtual
    chat_archive_s3_timeout_seconds: int = 60

    @field_validator("chat_retention_action")
    @classmethod
    def _check_retention_action(cls, v: str) -> str:
        v = (v or "archive").strip().lower()
        if v not in ("archive", "delete"):
            raise ValueError("CHAT_RETENTION_ACTION must be 'archive' or 'delete'")
        return v

    @field_validator("chat_archive_s3_addressing_style")
    @classmethod
    def _check_addressing_style(cls, v: str) -> str:
        v = (v or "auto").strip().lower()
        if v not in ("auto", "path", "virtual"):
            raise ValueError("CHAT_ARCHIVE_S3_ADDRESSING_STYLE must be auto, path or virtual")
        return v

    # Agent job settings
    agent_job_timeout: int = 600  # Default timeout for agent jobs (10 minutes)
    # Ceiling for a per-chat / per-agent job_timeout: a longer one is capped
    # here. Also sets arq's backstop timeout for the agent job functions.
    agent_job_timeout_max: int = 3600
    code_execution_timeout: int = 120  # Default timeout for code execution (2 minutes)

    # Workbench → sandbox sync caps (eager copy-in/copy-out per execution).
    # Files above the per-file cap are reported to the code as skipped rather
    # than materialized; the total cap bounds one execution's transfer.
    workbench_sync_max_file_bytes: int = 2 * 1024 * 1024
    workbench_sync_max_total_bytes: int = 32 * 1024 * 1024
    # Optional deployment-wide content filter ("namespace/name" of a Function)
    # applied to workbench file uploads, mirroring collection content filters
    # (e.g. the sensitive-image filter). Unset = uploads are not filtered.
    workbench_content_filter_function: Optional[str] = None
    # How long an upload waits for its content filter (a collection's
    # content_filter_function, or the workbench filter above) to answer,
    # time spent queued for a worker included. On expiry the upload is
    # rejected with 504 and nothing is stored: a filter that can't answer
    # never lets a file through unfiltered.
    content_filter_timeout_seconds: int = 60
    # Lazy fetch: files above the eager cap materialize as stubs and are
    # fetched on first read over the sandbox pause channel, bounded per fetch
    # and per execution.
    workbench_lazy_fetch_max_bytes: int = 64 * 1024 * 1024
    workbench_lazy_fetch_max_calls: int = 100
    # File references: cap on the resolved size of one {"$workbench": path}
    # tool-call parameter.
    workbench_ref_max_bytes: int = 10 * 1024 * 1024

    # Tool results above this many characters are truncated (structure-aware,
    # see services/tool_execution.truncate_tool_result) before they enter the
    # LLM context. Distinct from tool_result_max_size above, which caps what
    # the tool result store persists per row; same 100KB default so the two
    # caps stay aligned. ~25K tokens per result; deployments that want a
    # tighter per-turn budget can lower it via TOOL_RESULT_CONTEXT_MAX_SIZE.
    tool_result_context_max_size: int = 102400

    # Agent-to-agent delegation (call_agent_* tools). See issue #90.
    # - agent_delegate_timeout: how long a parent waits for a sub-agent result.
    #   Must be < agent_job_timeout in "block" mode, since the parent's own job
    #   clock keeps running while it waits.
    # - agent_subagent_queue: route delegated (depth > 0) agent jobs to the
    #   dedicated sub-agent queue so children never compete with top-level
    #   parents for worker slots. Requires the sub-agent worker process
    #   (SubAgentWorkerSettings); disable to restore single-queue routing.
    # - agent_max_delegation_depth: reject delegation chains deeper than this.
    # - agent_delegate_mode: "block" (parent holds its worker slot while
    #   awaiting the child) or "suspend" (parent job ends at delegation and a
    #   resume job continues the conversation when the children finish —
    #   frees the slot, at the cost of a brief stream gap between jobs).
    agent_delegate_timeout: int = 600
    agent_subagent_queue: bool = True
    agent_max_delegation_depth: int = 5
    agent_delegate_mode: str = "block"

    # Deferred tool rounds (pending completions) — expiry deadlines, in
    # seconds; 0 disables expiry for that kind (the entry waits forever).
    # - ask_user_timeout_seconds: how long an ask_user question stays open
    #   before the round resumes with a timeout error as the tool result.
    # - tool_approval_timeout_seconds: how long a pending tool approval stays
    #   open before it is auto-rejected. Default 0 keeps today's ask-forever
    #   behavior.
    # - agent_delegate_suspend_timeout_seconds: deadline for suspend-mode
    #   delegations (a child that dies without reporting back). Default 0
    #   keeps today's wait-forever behavior; block mode has its own
    #   agent_delegate_timeout above.
    ask_user_timeout_seconds: int = 86400
    tool_approval_timeout_seconds: int = 0
    agent_delegate_suspend_timeout_seconds: int = 0

    # Background work and wakeups (`background` system tool; ADR
    # 2026-10-11-background-jobs-and-wakeups). A background code run that
    # the agent waits on gets this grace on top of code_execution_timeout
    # before the round resumes with a timeout error; results delivered into
    # the chat are clipped to background_job_result_max_chars (the full
    # result stays readable via background_job_status for 24h). Timers may
    # be set at most wakeup_max_delay_seconds ahead; watches live
    # watch_default_ttl_seconds unless asked otherwise (capped at
    # watch_max_ttl_seconds) and accept payloads up to watch_max_payload_bytes.
    background_job_grace_seconds: int = 120
    background_job_result_max_chars: int = 16000
    wakeup_max_delay_seconds: int = 7 * 86400
    watch_default_ttl_seconds: int = 86400
    watch_max_ttl_seconds: int = 30 * 86400
    watch_max_payload_bytes: int = 65536

    # MCP servers (see ADR 2026-10-09-mcp-client). A server's tool list is
    # cached in-process so a chat turn doesn't re-handshake with every
    # enabled server; a failed listing is cached for a shorter time so a dead
    # server degrades to "no tools" without being retried on every message.
    # 0 disables that cache.
    mcp_tool_list_ttl_seconds: int = 60
    mcp_tool_list_failure_ttl_seconds: int = 15
    # Bounds on one tools/list discovery: pages followed and wall time, so
    # a server that pages forever (or repeats a cursor) is dropped from the
    # chat's tool list instead of holding the turn.
    mcp_list_tools_max_pages: int = 50
    mcp_list_tools_total_timeout_seconds: int = 60
    # MCP Tasks (ADR 2026-10-12-mcp-tasks). A tool call the server answers
    # with a task is polled inline for up to this long (and never longer
    # than the server's own timeout); still running after that, it is kept
    # and polled in the background, its result delivered into the chat.
    mcp_task_inline_wait_seconds: int = 20
    # Background polling honours the server's pollIntervalMs within these
    # bounds; the sweep itself runs every few seconds.
    mcp_task_min_poll_seconds: int = 5
    mcp_task_max_poll_seconds: int = 300
    mcp_task_default_poll_seconds: int = 10
    # Consecutive polls that cannot reach the server before the task is
    # reported failed (backing off in between).
    mcp_task_max_poll_failures: int = 10
    # A task the server gave no ttlMs is given up after this long.
    mcp_task_max_age_seconds: int = 7 * 86400
    mcp_task_poll_concurrency: int = 10

    # Package repositories (ADR 2026-10-13-package-repositories): how often
    # each repository's index is refreshed, and bounds on what is fetched.
    # Plain http only for local development.
    package_repository_refresh_hours: int = 6
    package_repository_timeout_seconds: int = 20
    package_repository_max_bytes: int = 2 * 1024 * 1024
    package_repositories_allow_http: bool = False

    # LLM cost (docs/design/usage-cost.md): the deployment ratecard, in
    # LiteLLM's model_prices_and_context_window.json format. Any of a file
    # path, inline JSON, or an https URL refreshed from the worker cron (the
    # last good copy kept in the database). Where several are set, inline
    # JSON wins over the file, the file over the URL. None set and no
    # provider pricing: calls are recorded unpriced.
    model_prices_file: Optional[str] = None
    model_prices_json: Optional[str] = None
    model_prices_url: Optional[str] = None
    model_prices_refresh_hours: int = 24
    model_prices_timeout_seconds: int = 30
    model_prices_max_bytes: int = 16 * 1024 * 1024

    # Encryption
    encryption_key: Optional[str] = None  # Fernet key for encrypting sensitive data

    # Superadmin
    superadmin_email: Optional[str] = None  # Email for superadmin user
    superadmin_password: Optional[str] = None  # Seed password for superadmin (used when auth_mode includes password)

    # How long a password setup/reset link stays valid (hours). One setting
    # for both: an admin's "reset link" and the operator's `setup-link` CLI
    # mint the same single-use token.
    password_reset_token_ttl_hours: int = 24

    # Authentication mode: "otp", "password", or "password+otp"
    # otp: email OTP only (default, requires SMTP)
    # password: password only (works airgapped, no SMTP needed)
    # password+otp: both required (password + email-OTP as liveness check)
    auth_mode: str = "otp"

    # Role assigned to users auto-provisioned via POST /auth/token/exchange
    token_exchange_default_role: str = "GuestUsers"

    # SSO (identity providers, docs/design/oidc-sso.md): allow http:// issuers
    # other than localhost. For a lab IdP only: over plain http the code and
    # tokens cross the network readable.
    oidc_allow_http: bool = False

    @field_validator("password_reset_token_ttl_hours")
    @classmethod
    def _validate_reset_ttl(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"PASSWORD_RESET_TOKEN_TTL_HOURS must be positive, got {v}")
        return v

    @field_validator("content_filter_timeout_seconds")
    @classmethod
    def _validate_content_filter_timeout(cls, v: int) -> int:
        # 0 would fall back to queue_default_timeout (the wait this bounds)
        # and a negative value rejects every filtered upload: refuse both.
        if v <= 0:
            raise ValueError(f"CONTENT_FILTER_TIMEOUT_SECONDS must be positive, got {v}")
        return v

    @field_validator("auth_mode")
    @classmethod
    def _validate_auth_mode(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in VALID_AUTH_MODES:
            raise ValueError(
                f"AUTH_MODE must be one of {sorted(VALID_AUTH_MODES)}, got {v!r}"
            )
        return normalized

    @model_validator(mode="after")
    def _default_max_execution_depth(self):
        # Default the nesting cap to the worker count so a single chain always
        # fits the pool (depth <= workers). Explicit values — including 0, which
        # disables the check — are left untouched.
        if self.max_execution_depth is None:
            self.max_execution_depth = self.default_worker_count
        return self

    # Domain (for generating external URLs, e.g., temp file URLs)
    domain: Optional[str] = None  # FQDN like "app.example.com"; localhost or None = no external URLs

    # OAuth authorization-code: bind the consent flow to the browser that started it
    # (an HttpOnly nonce cookie set by the authenticated begin call, checked at the
    # callback) to prevent account-linking/login-CSRF. Requires the console and API to be
    # same-site (they are in a normal single-domain deployment). MUST stay True in any
    # multi-user/production deployment. Set False ONLY for local dev where the console and
    # API run on different origins (e.g. console :51245 + API :8000) so the cookie can't
    # round-trip — disabling it there just removes the extra CSRF check for local testing.
    oauth_bind_browser_session: bool = True

    @property
    def public_base_url(self) -> str:
        """External origin the browser reaches this app at ('scheme://host', no trailing slash).

        Single source of truth for public URLs the browser must hit exactly (OAuth
        redirect/callback, postMessage target origin). Falls back to the backend port for
        local dev when no external domain is configured.
        """
        domain = (self.domain or "").strip()
        if not domain or domain.lower() in ("localhost", "127.0.0.1"):
            return f"http://localhost:{self.backend_port}"
        return f"https://{domain}"

    # Where the console is reached, if not at {public_base_url}/ui (e.g. the
    # compose setup serves it on its own port). Used to send people to it.
    console_url: Optional[str] = None

    @property
    def public_console_url(self) -> str:
        return (self.console_url or f"{self.public_base_url}/ui").rstrip("/")

    @property
    def token_issuer(self) -> str:
        """`iss` claim on RS256 access tokens; what verifiers configure as issuer."""
        return self.jwt_issuer.strip() or self.public_base_url

    # Operations metering (managed SaaS). Default-off; when enabled, every
    # operation (function/code/query/agent/upload/tool) increments a Redis
    # counter, the scheduler snapshots it to usage_periods, and a heartbeat
    # POSTs the CUMULATIVE period total to the Platform (sinas.metering/v2).
    # The Platform is the period authority: period id and boundaries arrive
    # exclusively in POST responses (no context endpoint) — first report is
    # sent under canonical_period_id "init". Pure emission: nothing is
    # enforced on the instance and nothing is pulled down. A dead endpoint
    # or Redis blip never affects platform behavior. With the platform
    # settings unset, counting stays local and no report is ever attempted.
    metering_enabled: bool = False
    platform_report_url: str = ""  # e.g. https://platform.example.com/api/sinas/metering/v1/reports
    platform_api_key: str = ""  # opaque instance token, sent as Authorization: Bearer — never logged
    metering_instance_id: str = ""  # defaults to `domain`; set explicitly in SaaS
    metering_snapshot_minutes: int = 5  # Redis -> usage_periods cadence
    metering_push_minutes: int = 15  # heartbeat cadence (jittered per instance)


    # OpenTelemetry (opt-in observability — e.g. Langwatch)
    otel_enabled: bool = False
    otel_exporter_endpoint: Optional[str] = None  # e.g. https://app.langwatch.ai/api/otel/v1/traces
    otel_exporter_headers: Optional[str] = None  # e.g. X-Auth-Token=lw_xxx
    otel_service_name: str = "sinas"
    otel_vendor: str = "langwatch"  # Vendor preset: "langwatch", "langfuse", "generic_otel"

    # Declarative Configuration
    config_file: Optional[str] = None  # Path to YAML config file
    auto_apply_config: bool = False  # Auto-apply config file on startup

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra env vars like POSTGRES_PASSWORD


settings = Settings()
