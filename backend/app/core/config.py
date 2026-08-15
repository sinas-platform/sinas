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
    max_tool_iterations: int = 25  # Max consecutive tool-call rounds before stopping

    # Tool result store
    tool_result_retention_days: int = int(os.getenv("TOOL_RESULT_RETENTION_DAYS", "30"))
    tool_result_max_inline: int = int(os.getenv("TOOL_RESULT_MAX_INLINE", "5"))  # Last N results kept inline
    tool_result_max_size: int = int(os.getenv("TOOL_RESULT_MAX_SIZE", "102400"))  # 100KB truncation limit

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

    # Pipeline runs (see ADR 2026-07-28-pipelines-triggers-and-linear-steps).
    # Runs are await-heavy orchestration → high concurrency is cheap.
    queue_pipeline_concurrency: int = 50
    pipeline_job_timeout: int = 1800  # hard ceiling for one queued run (incl. agent steps)
    pipeline_run_retention_days: int = 30  # pipeline_runs rows older than this are pruned

    # Agent job settings
    agent_job_timeout: int = 600  # Default timeout for agent jobs (10 minutes)
    code_execution_timeout: int = 120  # Default timeout for code execution (2 minutes)

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

    # Encryption
    encryption_key: Optional[str] = None  # Fernet key for encrypting sensitive data

    # Superadmin
    superadmin_email: Optional[str] = None  # Email for superadmin user
    superadmin_password: Optional[str] = None  # Seed password for superadmin (used when auth_mode includes password)

    # Authentication mode: "otp", "password", or "password+otp"
    # otp: email OTP only (default, requires SMTP)
    # password: password only (works airgapped, no SMTP needed)
    # password+otp: both required (password + email-OTP as liveness check)
    auth_mode: str = "otp"

    # Role assigned to users auto-provisioned via POST /auth/token/exchange
    token_exchange_default_role: str = "GuestUsers"

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

    @property
    def token_issuer(self) -> str:
        """`iss` claim on RS256 access tokens; what verifiers configure as issuer."""
        return self.jwt_issuer.strip() or self.public_base_url

    # Operations metering (managed SaaS). Default-off; when enabled, every
    # operation (function/code/query/agent/upload/tool) increments a Redis
    # counter, the scheduler snapshots it to usage_periods, and a heartbeat
    # pushes the CUMULATIVE period total to metering_endpoint. Pure emission:
    # nothing is enforced on the instance and nothing is pulled down. A dead
    # endpoint or Redis blip never affects platform behavior.
    metering_enabled: bool = False
    metering_endpoint: str = ""  # e.g. https://ops.example.com/v1/usage
    metering_api_key: str = ""  # sent as Authorization: Bearer <key>
    metering_instance_id: str = ""  # defaults to `domain`; set explicitly in SaaS
    metering_snapshot_minutes: int = 5  # Redis -> usage_periods cadence
    metering_push_minutes: int = 15  # heartbeat cadence (jittered per instance)

    # Component builder
    builder_url: str = "http://sinas-builder:3000"  # URL for esbuild compilation service

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
