"""Kubernetes-side data for the System page.

On a Kubernetes deployment there is no Docker socket, so the System page's
service list, per-service CPU/memory and the sandbox / shared container cards
come from the Kubernetes API instead:

- pods of the release (label `app.kubernetes.io/instance=<release>`), mapped
  to Sinas components by the chart's `app.kubernetes.io/component` label and,
  for executor pods, `sinas.type`;
- CPU/memory from `metrics.k8s.io` when metrics-server is installed. Without
  it (or without RBAC to read it) pods still show phase / ready / restarts /
  age, and the caller gets a human-readable reason instead of numbers.

Credentials are the pod's own ServiceAccount (the chart's executor-manager
SA, plus the `systemStats.rbac` Role for metrics). Every failure is turned
into a `K8sUnavailableError` carrying a reason the console can show — this module
never raises anything else to its callers.

The kubernetes client is blocking; the async helpers run it in a thread.
The System page polls several of these every few seconds, from every open
tab, so an apiserver the backend can't use (no RBAC, a NetworkPolicy that
drops its traffic, no token at all) must cost almost nothing:

- one attempt per call (no urllib3 retries), 2s to connect, 5s to read;
- a circuit breaker (`_Breaker`): after a failure the apiserver is not
  called again for a backoff window (60s, doubling to 5 minutes while it
  keeps failing); callers get the cached reason at once. Transport and
  credential failures open it for every call (they affect them all), a 403
  or other HTTP error only for the call that got it (missing metrics RBAC
  must not hide the pod list);
- single-flight: concurrent callers of the same call share one thread, and
  none waits more than `_WAIT_SECONDS` for it; the thread finishes in the
  background and its outcome feeds the breaker.

So at most one thread per kind of call is ever blocked on the apiserver,
however many tabs poll.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from app.core.config import settings

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 2.0  # seconds to open a connection to the apiserver
_READ_TIMEOUT = 5.0  # seconds for its answer
_API_TIMEOUT = (_CONNECT_TIMEOUT, _READ_TIMEOUT)
# Longest a request handler waits for a call in flight; past it the caller
# gets a reason and the call finishes in the background.
_WAIT_SECONDS = 2.5
BACKOFF_INITIAL_SECONDS = 60.0
BACKOFF_MAX_SECONDS = 300.0
_MIB = 1024 * 1024

SANDBOX_POD_TYPE = "sandbox-executor"
TRUSTED_POD_TYPE = "trusted-executor"


class K8sUnavailableError(Exception):
    """A Kubernetes source could not be read; `str(exc)` is shown to admins.

    `retry_after`: seconds until the backend tries the apiserver again (the
    breaker's window), None when it isn't backing off (e.g. a setting is
    missing; retrying changes nothing until it's fixed)."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


# ── Circuit breaker + single-flight ──────────────────────────────────

_ALL = "*"  # breaker key of failures that affect every call


class _Breaker:
    """Negative cache of apiserver failures, per call key plus one for all.

    Thread-safe: failures are recorded from the worker threads."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # key -> [open until (clock), reason, consecutive failures]
        self._state: dict[str, list[Any]] = {}

    def check(self, key: str) -> None:
        """Raise the cached failure while `key` (or every call) is backing off."""
        now = self._clock()
        with self._lock:
            for k in (_ALL, key):
                st = self._state.get(k)
                if st and st[0] > now:
                    raise K8sUnavailableError(st[1], retry_after=st[0] - now)

    def failure(self, key: str, reason: str) -> float:
        """Open `key` for the next window; returns the seconds left in it.

        Failures that land while it is already open (calls that were in
        flight together) don't lengthen it: only consecutive windows do."""
        now = self._clock()
        with self._lock:
            st = self._state.get(key)
            if st and st[0] > now:
                st[1] = reason
                return st[0] - now
            failures = (st[2] if st else 0) + 1
            window = min(BACKOFF_INITIAL_SECONDS * 2 ** (failures - 1), BACKOFF_MAX_SECONDS)
            self._state[key] = [now + window, reason, failures]
        logger.warning(
            "System page: Kubernetes API unavailable (%s); not calling it again for %ds",
            reason,
            window,
        )
        return window

    def success(self, key: str) -> None:
        """A call got through: the apiserver is reachable, close both."""
        with self._lock:
            had_key = self._state.pop(key, None)
            had_all = self._state.pop(_ALL, None)
        if had_key or had_all:
            logger.info("System page: Kubernetes API answering again")

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


_breaker = _Breaker()
# key -> (shared future of the call in flight, loop time it started)
_inflight: dict[str, tuple[asyncio.Future, float]] = {}

T = TypeVar("T")


def reset_breaker() -> None:
    """Forget cached failures and calls in flight (tests)."""
    _breaker.reset()
    _inflight.clear()


def _run_guarded(
    key: str, blocking: Callable[[], T], describe: Callable[[Exception], tuple[str | None, str]]
) -> T:
    """Run one blocking call (in a worker thread), feeding the breaker.

    `describe(exc)` -> (breaker key or None to not cache, admin reason)."""
    try:
        result = blocking()
    except K8sUnavailableError:
        raise
    except Exception as e:
        scope, reason = describe(e)
        retry_after = _breaker.failure(scope, reason) if scope else None
        raise K8sUnavailableError(reason, retry_after=retry_after) from e
    _breaker.success(key)
    return result


async def _call(
    key: str, blocking: Callable[[], T], describe: Callable[[Exception], tuple[str | None, str]]
) -> T:
    """`blocking()` in a thread, behind the breaker and single-flight."""
    _breaker.check(key)
    loop = asyncio.get_running_loop()
    entry = _inflight.get(key)
    if entry is None or entry[0].get_loop() is not loop:
        fut = asyncio.ensure_future(asyncio.to_thread(_run_guarded, key, blocking, describe))
        entry = (fut, loop.time())
        _inflight[key] = entry

        def _done(f: asyncio.Future, key: str = key) -> None:
            if _inflight.get(key, (None,))[0] is f:
                del _inflight[key]
            if not f.cancelled():
                f.exception()  # retrieved: no "never retrieved" log if nobody waits

        fut.add_done_callback(_done)
    fut, started = entry
    wait = max(0.0, _WAIT_SECONDS - (loop.time() - started))
    try:
        return await asyncio.wait_for(asyncio.shield(fut), wait)
    except TimeoutError:
        raise K8sUnavailableError(
            f"The Kubernetes API has not answered within {_WAIT_SECONDS:g}s; the backend keeps "
            "waiting for it in the background (a NetworkPolicy or firewall may be dropping "
            "the backend's traffic to the apiserver)."
        ) from None


def deployment_mode() -> str:
    """ "kubernetes" or "docker", derived from the executor settings.

    There is no separate deployment flag: a Kubernetes deploy is one whose
    executors are the k8s ones, or that the Helm chart stamped with
    K8S_RELEASE_NAME (always set by the chart, also with sandbox/trusted
    executors disabled or in-process).
    """
    if (
        settings.sandbox_executor.startswith("k8s")
        or settings.trusted_executor.startswith("k8s")
        or settings.k8s_release_name
    ):
        return "kubernetes"
    return "docker"


def release_namespace() -> str:
    """Namespace this process runs in — where the release's pods live."""
    ns = os.environ.get("POD_NAMESPACE")
    if ns:
        return ns
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
            return f.read().strip()
    except OSError:
        from app.services.executor._k8s_runtime import resolve_namespace

        return resolve_namespace()


def executor_namespace() -> str:
    """Namespace executor pods are created in (k8s_sandbox_namespace or ours)."""
    from app.services.executor._k8s_runtime import resolve_namespace

    return resolve_namespace()


_stats_apis: tuple[Any, Any] | None = None


def _apis() -> tuple[Any, Any]:
    """(CoreV1Api, CustomObjectsApi) for this module.

    Same credentials as the executor (its loaded config), but an API client
    of its own with urllib3's retries off: by default a connection that
    times out is tried 4 times, so one call to a blocked apiserver would
    take 4x the connect timeout. The breaker decides when to try again."""
    global _stats_apis
    if _stats_apis is None:
        from kubernetes import client

        from app.services.executor._k8s_runtime import _get_clients

        _get_clients()  # loads the in-cluster config (or kubeconfig) as the default
        cfg = client.Configuration.get_default_copy()
        cfg.retries = False
        api = client.ApiClient(cfg)
        _stats_apis = (client.CoreV1Api(api), client.CustomObjectsApi(api))
    return _stats_apis


def _selector(*extra: str) -> str:
    """Label selector scoped to this release.

    Without a release name there is no way to tell this deployment's pods
    from anything else in the namespace, so refuse rather than list them all.
    """
    if not settings.k8s_release_name:
        raise K8sUnavailableError(
            "K8S_RELEASE_NAME is not set, so this deployment's pods can't be told "
            "apart from others in the namespace (the Helm chart sets it)."
        )
    return ",".join([f"app.kubernetes.io/instance={settings.k8s_release_name}", *extra])


def _unwrap(e: BaseException) -> BaseException:
    """The transport error under urllib3's MaxRetryError / a raise-from."""
    inner = getattr(e, "reason", None)
    if isinstance(inner, BaseException):
        return inner
    return e.__cause__ or e


def _describe(e: Exception, what: str, namespace: str, key: str) -> tuple[str | None, str]:
    """(breaker key, admin-facing reason) for a failed apiserver call.

    Status only, never a response body or a stack trace."""
    from kubernetes.client.exceptions import ApiException
    from kubernetes.config.config_exception import ConfigException

    if isinstance(e, ApiException):
        if e.status == 401:
            return _ALL, (
                "The Kubernetes API rejected the backend's credentials (HTTP 401): its "
                "service account token is missing, expired or not valid for this cluster."
            )
        if e.status == 403:
            return key, (
                f"The backend's service account isn't allowed to {what} in namespace "
                f"'{namespace}' (HTTP 403). Enable systemStats.rbac in the Helm chart (or "
                "grant get/list on that resource), or turn the cluster part of the System "
                "page off with systemStats.kubernetes=false."
            )
        return key, f"Kubernetes API refused to {what} (HTTP {e.status} {e.reason or ''})".strip()
    if isinstance(e, ConfigException):
        return _ALL, (
            "The backend has no Kubernetes API credentials (no service account token "
            f"is mounted in its pod): {e}"
        )
    inner = _unwrap(e)
    name = type(inner).__name__
    if "Timeout" in name or isinstance(inner, TimeoutError):
        return _ALL, (
            f"The Kubernetes API did not answer in time ({name}): a NetworkPolicy or "
            "firewall may be blocking the backend's traffic to the apiserver."
        )
    detail = str(inner).strip()
    if len(detail) > 160:
        detail = detail[:157] + "..."
    return _ALL, f"Kubernetes API not reachable ({name}{': ' + detail if detail else ''})"


# ── Parsing ──────────────────────────────────────────────────────────


def _quantity(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        from kubernetes.utils import parse_quantity

        return float(parse_quantity(value))
    except Exception:
        return None


def _component(pod: Any) -> str:
    labels = pod.metadata.labels or {}
    pod_type = labels.get("sinas.type")
    # Same service names the Docker path uses for executor containers.
    if pod_type == TRUSTED_POD_TYPE:
        return "shared"
    if pod_type == SANDBOX_POD_TYPE:
        return "sandbox"
    return labels.get("app.kubernetes.io/component") or pod.metadata.name


def _limits(pod: Any) -> tuple[float | None, float | None]:
    """Summed (cpu cores, memory bytes) limits; None if any container is unbounded."""
    cpu_total: float | None = 0.0
    mem_total: float | None = 0.0
    for c in (pod.spec.containers if pod.spec else None) or []:
        limits = (c.resources.limits if c.resources else None) or {}
        cpu = _quantity(limits.get("cpu"))
        mem = _quantity(limits.get("memory"))
        cpu_total = None if cpu is None or cpu_total is None else cpu_total + cpu
        mem_total = None if mem is None or mem_total is None else mem_total + mem
    return cpu_total or None, mem_total or None


def _status(pod: Any) -> tuple[str, str | None]:
    """Docker-like status word + the kubelet's reason when one explains it."""
    if pod.metadata.deletion_timestamp:
        return "terminating", None
    phase = (pod.status.phase if pod.status else None) or "Unknown"
    waiting = None
    for cs in (pod.status.container_statuses if pod.status else None) or []:
        if cs.state and cs.state.waiting and cs.state.waiting.reason:
            waiting = cs.state.waiting.reason
            break
    if phase == "Running":
        if waiting == "CrashLoopBackOff":
            return "restarting", waiting
        return "running", waiting
    mapping = {"Pending": "pending", "Succeeded": "completed", "Failed": "failed"}
    return mapping.get(phase, "unknown"), waiting or (pod.status.reason if pod.status else None)


def pod_info(pod: Any, now: datetime | None = None) -> dict[str, Any]:
    """One pod as a System-page service row (same keys as the Docker rows)."""
    now = now or datetime.now(timezone.utc)
    status, reason = _status(pod)
    statuses = (pod.status.container_statuses if pod.status else None) or []
    ready_count = sum(1 for cs in statuses if cs.ready)
    total = len(pod.spec.containers) if pod.spec and pod.spec.containers else len(statuses)
    restarts = sum(cs.restart_count or 0 for cs in statuses)

    started = (pod.status.start_time if pod.status else None) or pod.metadata.creation_timestamp
    uptime = int((now - started).total_seconds()) if started and status == "running" else 0
    created = pod.metadata.creation_timestamp
    age = int((now - created).total_seconds()) if created else None

    if status != "running":
        health = "none"
    elif total and ready_count == total:
        health = "healthy"
    else:
        # A fresh pod that hasn't passed readiness yet isn't "unhealthy".
        health = "starting" if (age or 0) < 120 and restarts == 0 else "unhealthy"

    return {
        "name": pod.metadata.name,
        "service": _component(pod),
        "status": status,
        "status_reason": reason,
        "health": health,
        "uptime_seconds": max(uptime, 0),
        "age_seconds": age,
        "created_at": created.isoformat() if created else "",
        "ready": f"{ready_count}/{total}",
        "restarts": restarts,
        "node": pod.spec.node_name if pod.spec else None,
    }


def _apply_metrics(info: dict[str, Any], pod: Any, usage: dict[str, float]) -> None:
    """Docker-compatible cpu_percent (100 = one core) + memory block."""
    cpu_limit, mem_limit = _limits(pod)
    cores = usage.get("cpu", 0.0)
    mem = usage.get("memory", 0.0)
    info["cpu_percent"] = round(cores * 100, 1)
    info["cpu_limit_cores"] = round(cpu_limit, 2) if cpu_limit else None
    info["memory"] = {
        "used_mb": round(mem / _MIB, 1),
        "limit_mb": round(mem_limit / _MIB, 1) if mem_limit else None,
        "percent": round(mem / mem_limit * 100, 1) if mem_limit else None,
    }


# ── Blocking API calls (run via asyncio.to_thread) ───────────────────


def _list_pods_blocking(namespace: str, selector: str) -> list[Any]:
    core, _ = _apis()
    return core.list_namespaced_pod(
        namespace=namespace, label_selector=selector, _request_timeout=_API_TIMEOUT
    ).items


def _pod_metrics_blocking(namespace: str, selector: str) -> dict[str, dict[str, float]]:
    """pod name → {"cpu": cores, "memory": bytes}."""
    _, custom = _apis()
    resp = custom.list_namespaced_custom_object(
        group="metrics.k8s.io",
        version="v1beta1",
        namespace=namespace,
        plural="pods",
        label_selector=selector,
        _request_timeout=_API_TIMEOUT,
    )
    out: dict[str, dict[str, float]] = {}
    for item in resp.get("items", []):
        name = (item.get("metadata") or {}).get("name")
        if not name:
            continue
        cpu = mem = 0.0
        for c in item.get("containers") or []:
            usage = c.get("usage") or {}
            cpu += _quantity(usage.get("cpu")) or 0.0
            mem += _quantity(usage.get("memory")) or 0.0
        out[name] = {"cpu": cpu, "memory": mem}
    return out


async def _list_pods(namespace: str, selector: str) -> list[Any]:
    key = f"pods:{namespace}:{selector}"
    return await _call(
        key,
        lambda: _list_pods_blocking(namespace, selector),
        lambda e: _describe(e, "list pods", namespace, key),
    )


async def _pod_metrics(namespace: str, selector: str) -> dict[str, dict[str, float]]:
    key = f"metrics:{namespace}:{selector}"

    def describe(e: Exception) -> tuple[str | None, str]:
        from kubernetes.client.exceptions import ApiException

        if isinstance(e, ApiException) and e.status == 404:
            return key, (
                "CPU and memory need metrics-server, which is not installed in "
                "this cluster (the metrics.k8s.io API was not found)."
            )
        if isinstance(e, ApiException) and e.status == 503:
            return key, (
                "CPU and memory unavailable: the metrics.k8s.io API is registered "
                "but metrics-server is not serving yet."
            )
        return _describe(e, "read pod metrics (pods.metrics.k8s.io)", namespace, key)

    return await _call(key, lambda: _pod_metrics_blocking(namespace, selector), describe)


# ── Async entry points ───────────────────────────────────────────────


async def list_services(
    include_stats: bool,
) -> tuple[list[dict[str, Any]], K8sUnavailableError | None, str | None]:
    """Release pods as service rows.

    Returns (services, why the pods couldn't be listed, metrics_unavailable_reason).
    Ephemeral sandbox pods are left out — they live for one execution and are
    shown on the sandbox card instead; warm trusted pods are included, like
    the Docker shared containers.
    """
    namespace = release_namespace()
    try:
        selector = _selector()
        pods = await _list_pods(namespace, selector)
    except K8sUnavailableError as e:
        return [], e, None

    pods = [p for p in pods if (p.metadata.labels or {}).get("sinas.type") != SANDBOX_POD_TYPE]
    now = datetime.now(timezone.utc)
    services = [pod_info(p, now) for p in pods]

    metrics_reason = None
    if include_stats:
        try:
            usage = await _pod_metrics(namespace, selector)
        except K8sUnavailableError as e:
            metrics_reason = str(e)
        else:
            for info, pod in zip(services, pods, strict=True):
                if info["status"] == "running" and info["name"] in usage:
                    _apply_metrics(info, pod, usage[info["name"]])

    priority = {"redis": 0, "postgres": 1, "pgbouncer": 2, "clickhouse": 3, "backend": 4}
    services.sort(key=lambda s: (priority.get(s["service"], 99), s["name"]))
    return services, None, metrics_reason


async def list_executor_pods(pod_type: str) -> list[dict[str, Any]]:
    """Executor pods of one kind (sandbox / trusted). Raises K8sUnavailableError."""
    selector = _selector(f"sinas.type={pod_type}")
    namespace = executor_namespace()
    pods = await _list_pods(namespace, selector)
    now = datetime.now(timezone.utc)
    return sorted((pod_info(p, now) for p in pods), key=lambda i: i["name"])
