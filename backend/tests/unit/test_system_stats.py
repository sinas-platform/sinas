"""System page stats on Docker and Kubernetes.

On Kubernetes there is no Docker socket: services come from the release's
pods and CPU/memory from metrics.k8s.io. The k8s API is mocked here at
`k8s_stats._apis`; the cases are metrics present, metrics-server absent and
RBAC forbidden. Docker mode is pinned to its previous behaviour, and every
failing source must degrade to an `*_unavailable_reason`, never a 500.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import docker
import pytest
from kubernetes import client as k8s
from kubernetes.client.exceptions import ApiException

from app.api.v1.endpoints import system as system_endpoint
from app.core.config import settings
from app.services import k8s_stats
from tests.conftest import auth_headers

NS = "sinas-test"
REL = "rel"


# ── Fakes ────────────────────────────────────────────────────────────


def _pod(
    name: str,
    component: str | None = None,
    sinas_type: str | None = None,
    phase: str = "Running",
    ready: bool = True,
    restarts: int = 0,
    limits: dict | None = None,
    age: int = 600,
    waiting: str | None = None,
) -> k8s.V1Pod:
    labels = {"app.kubernetes.io/instance": REL}
    if component:
        labels["app.kubernetes.io/component"] = component
    if sinas_type:
        labels["sinas.type"] = sinas_type
    created = datetime.now(timezone.utc) - timedelta(seconds=age)
    state = k8s.V1ContainerState(
        waiting=k8s.V1ContainerStateWaiting(reason=waiting) if waiting else None
    )
    return k8s.V1Pod(
        metadata=k8s.V1ObjectMeta(name=name, labels=labels, creation_timestamp=created),
        spec=k8s.V1PodSpec(
            node_name="node-1",
            containers=[
                k8s.V1Container(
                    name="main",
                    resources=k8s.V1ResourceRequirements(
                        limits=limits if limits is not None else {"cpu": "1", "memory": "1Gi"}
                    ),
                )
            ],
        ),
        status=k8s.V1PodStatus(
            phase=phase,
            start_time=created,
            container_statuses=[
                k8s.V1ContainerStatus(
                    name="main",
                    image="img",
                    image_id="",
                    ready=ready,
                    restart_count=restarts,
                    state=state,
                )
            ],
        ),
    )


RELEASE_PODS = [
    _pod("backend-abc", "backend"),
    _pod("queue-worker-def", "queue-worker"),
    _pod("redis-0", "redis", limits={}),  # no limits: memory percent unknown
    _pod("rel-trusted-worker-0", sinas_type="trusted-executor"),
    _pod("sinas-sbx-123", sinas_type="sandbox-executor", phase="Pending"),
]

METRICS = {
    "items": [
        {
            "metadata": {"name": "backend-abc"},
            "containers": [{"usage": {"cpu": "250m", "memory": "262144Ki"}}],
        },
        {
            "metadata": {"name": "queue-worker-def"},
            "containers": [{"usage": {"cpu": "50000000n", "memory": "128Mi"}}],
        },
        {
            "metadata": {"name": "redis-0"},
            "containers": [{"usage": {"cpu": "1m", "memory": "64Mi"}}],
        },
    ]
}


class FakeCore:
    def __init__(self, pods=RELEASE_PODS, error: Exception | None = None):
        self.pods = pods
        self.error = error
        self.calls: list[dict] = []

    def list_namespaced_pod(self, namespace, label_selector="", _request_timeout=None):
        self.calls.append({"namespace": namespace, "selector": label_selector})
        if self.error:
            raise self.error
        wanted = dict(p.split("=", 1) for p in label_selector.split(",") if p)
        items = [
            p
            for p in self.pods
            if all((p.metadata.labels or {}).get(k) == v for k, v in wanted.items())
        ]
        return SimpleNamespace(items=items)


class FakeCustom:
    def __init__(self, metrics=METRICS, error: Exception | None = None):
        self.metrics = metrics
        self.error = error
        self.calls: list[dict] = []

    def list_namespaced_custom_object(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.metrics


@pytest.fixture
def k8s_mode(monkeypatch):
    """Kubernetes deployment as the Helm chart configures it, API mocked."""
    monkeypatch.setattr(settings, "sandbox_executor", "k8s_pod")
    monkeypatch.setattr(settings, "trusted_executor", "k8s_shared")
    monkeypatch.setattr(settings, "k8s_release_name", REL)
    monkeypatch.setattr(settings, "k8s_sandbox_namespace", "")
    monkeypatch.setattr(settings, "code_execution_enabled", True)
    monkeypatch.setenv("POD_NAMESPACE", NS)
    # The breaker is process-wide: no failure cached by an earlier test.
    k8s_stats.reset_breaker()

    def no_docker(*a, **kw):
        raise AssertionError("Docker must not be touched on Kubernetes")

    monkeypatch.setattr(system_endpoint.docker, "from_env", no_docker)

    apis = SimpleNamespace(core=FakeCore(), custom=FakeCustom())
    monkeypatch.setattr(k8s_stats, "_apis", lambda: (apis.core, apis.custom))
    return apis


@pytest.fixture
def docker_mode(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_executor", "docker_pool")
    monkeypatch.setattr(settings, "trusted_executor", "docker_shared")
    monkeypatch.setattr(settings, "k8s_release_name", "")
    monkeypatch.setattr(settings, "code_execution_enabled", True)
    k8s_stats.reset_breaker()

    def no_k8s():
        raise AssertionError("The Kubernetes API must not be touched on Docker")

    monkeypatch.setattr(k8s_stats, "_apis", no_k8s)


async def _health(client, user, include_stats=True):
    resp = await client.get(
        "/api/v1/system/health",
        params={"include_stats": include_stats},
        headers=auth_headers(user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Deployment mode ──────────────────────────────────────────────────


class TestDeploymentMode:
    def test_docker_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "sandbox_executor", "docker_pool")
        monkeypatch.setattr(settings, "trusted_executor", "docker_shared")
        monkeypatch.setattr(settings, "k8s_release_name", "")
        assert k8s_stats.deployment_mode() == "docker"

    @pytest.mark.parametrize(
        "sandbox,trusted,release",
        [
            ("k8s_pod", "inprocess", ""),
            ("disabled", "k8s_shared", ""),
            ("disabled", "inprocess", "rel"),
        ],
    )
    def test_kubernetes(self, monkeypatch, sandbox, trusted, release):
        monkeypatch.setattr(settings, "sandbox_executor", sandbox)
        monkeypatch.setattr(settings, "trusted_executor", trusted)
        monkeypatch.setattr(settings, "k8s_release_name", release)
        assert k8s_stats.deployment_mode() == "kubernetes"


# ── Kubernetes: /system/health ───────────────────────────────────────


class TestKubernetesHealth:
    async def test_metrics_present(self, client, admin_user, k8s_mode):
        body = await _health(client, admin_user)

        assert body["deployment"] == "kubernetes"
        assert body["executors"] == {"sandbox": "k8s_pod", "trusted": "k8s_shared"}
        assert body["services_unavailable_reason"] is None
        assert body["metrics_unavailable_reason"] is None

        by_name = {s["name"]: s for s in body["services"]}
        # Ephemeral sandbox pods belong on the sandbox card, not here.
        assert set(by_name) == {
            "backend-abc",
            "queue-worker-def",
            "redis-0",
            "rel-trusted-worker-0",
        }
        assert by_name["backend-abc"]["service"] == "backend"
        assert by_name["rel-trusted-worker-0"]["service"] == "shared"
        assert body["services"][0]["service"] == "redis"  # infra sorts first

        backend = by_name["backend-abc"]
        assert backend["status"] == "running"
        assert backend["health"] == "healthy"
        assert backend["ready"] == "1/1"
        assert backend["restarts"] == 0
        assert backend["uptime_seconds"] >= 600
        # Docker semantics: 100% = one core.
        assert backend["cpu_percent"] == 25.0
        assert backend["memory"] == {"used_mb": 256.0, "limit_mb": 1024.0, "percent": 25.0}
        assert by_name["queue-worker-def"]["cpu_percent"] == 5.0
        assert by_name["redis-0"]["memory"] == {"used_mb": 64.0, "limit_mb": None, "percent": None}

        # Scoped to the release in our own namespace, via the service account.
        assert k8s_mode.core.calls[0] == {
            "namespace": NS,
            "selector": f"app.kubernetes.io/instance={REL}",
        }
        metrics_call = k8s_mode.custom.calls[0]
        assert (metrics_call["group"], metrics_call["plural"], metrics_call["namespace"]) == (
            "metrics.k8s.io",
            "pods",
            NS,
        )

    async def test_fast_poll_skips_metrics(self, client, admin_user, k8s_mode):
        body = await _health(client, admin_user, include_stats=False)
        assert len(body["services"]) == 4
        assert k8s_mode.custom.calls == []
        assert "cpu_percent" not in body["services"][0]

    async def test_metrics_server_absent(self, client, admin_user, k8s_mode):
        k8s_mode.custom.error = ApiException(status=404, reason="Not Found")
        body = await _health(client, admin_user)

        assert "metrics-server" in body["metrics_unavailable_reason"]
        assert body["services_unavailable_reason"] is None
        backend = next(s for s in body["services"] if s["name"] == "backend-abc")
        # Pod status still there, without numbers.
        assert backend["status"] == "running"
        assert backend["ready"] == "1/1"
        assert backend["restarts"] == 0
        assert "cpu_percent" not in backend and "memory" not in backend

    async def test_metrics_api_not_serving(self, client, admin_user, k8s_mode):
        k8s_mode.custom.error = ApiException(status=503, reason="Service Unavailable")
        body = await _health(client, admin_user)
        assert "not serving" in body["metrics_unavailable_reason"]
        assert len(body["services"]) == 4

    async def test_metrics_rbac_forbidden(self, client, admin_user, k8s_mode):
        k8s_mode.custom.error = ApiException(status=403, reason="Forbidden")
        body = await _health(client, admin_user)
        reason = body["metrics_unavailable_reason"]
        assert "pods.metrics.k8s.io" in reason and NS in reason and "systemStats.rbac" in reason
        assert len(body["services"]) == 4

    async def test_pods_rbac_forbidden(self, client, admin_user, k8s_mode):
        k8s_mode.core.error = ApiException(status=403, reason="Forbidden")
        body = await _health(client, admin_user)
        assert body["services"] == []
        reason = body["services_unavailable_reason"]
        assert "isn't allowed to list pods" in reason and NS in reason
        # The rest of the page still renders.
        assert "host" in body and isinstance(body["warnings"], list)

    async def test_api_unreachable(self, client, admin_user, k8s_mode):
        k8s_mode.core.error = ConnectionRefusedError("apiserver down")
        body = await _health(client, admin_user)
        assert body["services"] == []
        assert "not reachable" in body["services_unavailable_reason"]

    async def test_unexpected_failure_degrades(self, client, admin_user, k8s_mode, monkeypatch):
        async def boom(include_stats):
            raise RuntimeError("bug")

        monkeypatch.setattr(k8s_stats, "list_services", boom)
        body = await _health(client, admin_user)
        assert body["services"] == []
        assert body["services_unavailable_reason"]

    async def test_no_release_name_lists_nothing(self, client, admin_user, k8s_mode, monkeypatch):
        """Kubernetes mode via the k8s executors but no K8S_RELEASE_NAME: an
        unfiltered list would show every app in the namespace, so refuse."""
        monkeypatch.setattr(settings, "k8s_release_name", "")
        k8s_mode.core.pods = RELEASE_PODS + [_pod("someone-elses-app", "web")]

        body = await _health(client, admin_user)
        assert body["deployment"] == "kubernetes"
        assert body["services"] == []
        assert "K8S_RELEASE_NAME" in body["services_unavailable_reason"]
        assert not any("someone-elses-app" in w["message"] for w in body["warnings"])

        h = auth_headers(admin_user)
        stats = (await client.get("/api/v1/containers/stats", headers=h)).json()
        count = (await client.get("/api/v1/workers/count", headers=h)).json()
        assert stats["total"] == 0 and "K8S_RELEASE_NAME" in stats["unavailable_reason"]
        assert count["count"] == 0 and "K8S_RELEASE_NAME" in count["unavailable_reason"]
        assert k8s_mode.core.calls == [] and k8s_mode.custom.calls == []

    async def test_services_turned_off_skip_the_api(self, client, admin_user, k8s_mode, monkeypatch):
        """systemStats.rbac=false (SYSTEM_STATS_KUBERNETES=false): no API call
        at all (there may be no token or RBAC), and no error: the console
        shows the cluster status as turned off, with the reason."""
        monkeypatch.setattr(settings, "system_stats_kubernetes", False)
        k8s_mode.core.error = AssertionError("pods must not be listed")
        k8s_mode.custom.error = AssertionError("metrics must not be read")

        body = await _health(client, admin_user)
        assert body["deployment"] == "kubernetes"
        assert body["services_enabled"] is False
        assert body["services"] == []
        assert body["services_unavailable_reason"] is None
        assert body["metrics_unavailable_reason"] is None
        # ...but the console can say why there is no cluster status.
        assert "turned off" in body["cluster_unavailable_reason"]
        assert body["cluster_retry_after_seconds"] is None
        assert k8s_mode.core.calls == [] and k8s_mode.custom.calls == []
        # The rest of the page still renders.
        assert "host" in body and isinstance(body["warnings"], list)

    async def test_code_execution_off_no_api_identity(self, client, admin_user, k8s_mode, monkeypatch):
        """The chart with features.codeExecution=false and systemStats.rbac=false:
        executors disabled, no token. Neither the page nor the executor cards
        touch the Kubernetes API."""
        monkeypatch.setattr(settings, "code_execution_enabled", False)
        monkeypatch.setattr(settings, "sandbox_executor", "disabled")
        monkeypatch.setattr(settings, "trusted_executor", "disabled")
        monkeypatch.setattr(settings, "system_stats_kubernetes", False)

        body = await _health(client, admin_user)
        # K8S_RELEASE_NAME alone still marks a Kubernetes deployment.
        assert body["deployment"] == "kubernetes"
        assert body["services_enabled"] is False and body["services"] == []

        h = auth_headers(admin_user)
        stats = (await client.get("/api/v1/containers/stats", headers=h)).json()
        count = (await client.get("/api/v1/workers/count", headers=h)).json()
        assert "Code execution is disabled" in stats["unavailable_reason"]
        assert "Code execution is disabled" in count["unavailable_reason"]
        assert k8s_mode.core.calls == [] and k8s_mode.custom.calls == []

    async def test_cluster_unavailable_rest_of_page_present(
        self, client, admin_user, k8s_mode
    ):
        """A blocked apiserver: the cluster part says why, once per backoff
        window; everything else in the response is unaffected, and every
        later poll (health, both executor cards) is answered from the
        breaker without calling the API."""
        from urllib3.exceptions import ConnectTimeoutError, MaxRetryError

        k8s_mode.core.error = MaxRetryError(None, "/api", ConnectTimeoutError("timed out"))
        body = await _health(client, admin_user)
        assert body["services"] == []
        assert "did not answer in time" in body["cluster_unavailable_reason"]
        assert body["services_unavailable_reason"] == body["cluster_unavailable_reason"]
        assert body["cluster_retry_after_seconds"] == 60
        # the rest is there
        assert body["host"]["disk_total_gb"] is not None
        assert isinstance(body["warnings"], list) and body["redis"] is not None
        assert body["deployment"] == "kubernetes" and body["services_enabled"] is True

        h = auth_headers(admin_user)
        for _ in range(3):
            body = await _health(client, admin_user)
            assert "did not answer in time" in body["cluster_unavailable_reason"]
            stats = (await client.get("/api/v1/containers/stats", headers=h)).json()
            count = (await client.get("/api/v1/workers/count", headers=h)).json()
            assert stats["cluster_unavailable"] is True and 0 < stats["retry_after_seconds"] <= 60
            assert count["cluster_unavailable"] is True and 0 < count["retry_after_seconds"] <= 60
            assert "did not answer in time" in count["unavailable_reason"]
        assert len(k8s_mode.core.calls) == 1 and k8s_mode.custom.calls == []

    async def test_services_on_by_default(self, client, admin_user, k8s_mode):
        body = await _health(client, admin_user)
        assert body["services_enabled"] is True
        assert len(body["services"]) == 4
        assert body["cluster_unavailable_reason"] is None

    async def test_restart_rejected_clearly(self, client, admin_user, k8s_mode):
        resp = await client.post(
            "/api/v1/system/containers/backend-abc/restart", headers=auth_headers(admin_user)
        )
        assert resp.status_code == 409
        assert "kubectl" in resp.json()["detail"]


class TestPodStatus:
    def test_crashloop_is_restarting_and_warned(self):
        pod = _pod("redis-0", "redis", ready=False, restarts=7, waiting="CrashLoopBackOff")
        info = k8s_stats.pod_info(pod)
        assert info["status"] == "restarting"
        assert info["status_reason"] == "CrashLoopBackOff"
        assert info["restarts"] == 7

        warnings = system_endpoint._generate_warnings([info], [], 0, {}, {})
        assert any(w["level"] == "critical" and "redis" in w["message"] for w in warnings)

    def test_pending_carries_reason(self):
        info = k8s_stats.pod_info(
            _pod("x", "backend", phase="Pending", ready=False, waiting="ImagePullBackOff")
        )
        assert info["status"] == "pending"
        assert info["status_reason"] == "ImagePullBackOff"
        assert info["health"] == "none"
        assert info["uptime_seconds"] == 0

    def test_unready_running_pod(self):
        fresh = k8s_stats.pod_info(_pod("a", "backend", ready=False, age=10))
        stale = k8s_stats.pod_info(_pod("b", "backend", ready=False, restarts=2))
        assert fresh["health"] == "starting"
        assert stale["health"] == "unhealthy"

    def test_terminating(self):
        pod = _pod("a", "backend")
        pod.metadata.deletion_timestamp = datetime.now(timezone.utc)
        assert k8s_stats.pod_info(pod)["status"] == "terminating"


# ── Kubernetes: sandbox / shared cards ───────────────────────────────


class TestKubernetesExecutorCards:
    async def test_sandbox_card_lists_inflight_pods(self, client, admin_user, k8s_mode):
        resp = await client.get("/api/v1/containers/stats", headers=auth_headers(admin_user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["executor"] == "k8s_pod" and body["deployment"] == "kubernetes"
        assert body["scalable"] is False and body["note"]
        assert body["total"] == body["in_use"] == 1
        assert body["in_use_containers"][0]["name"] == "sinas-sbx-123"
        assert body["in_use_containers"][0]["status"] == "pending"
        assert k8s_mode.core.calls[-1]["selector"] == (
            f"app.kubernetes.io/instance={REL},sinas.type=sandbox-executor"
        )

    async def test_sandbox_card_degrades(self, client, admin_user, k8s_mode):
        k8s_mode.core.error = ApiException(status=403, reason="Forbidden")
        resp = await client.get("/api/v1/containers/stats", headers=auth_headers(admin_user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0 and "isn't allowed to list pods" in body["unavailable_reason"]

    async def test_finished_sandbox_pods_not_counted_busy(self, client, admin_user, k8s_mode):
        """A pod stopped by activeDeadlineSeconds lingers as Failed; a leaked
        one as Succeeded. Neither is an execution in flight."""
        k8s_mode.core.pods = RELEASE_PODS + [
            _pod("sinas-sbx-done", sinas_type="sandbox-executor", phase="Succeeded", ready=False),
            _pod("sinas-sbx-deadline", sinas_type="sandbox-executor", phase="Failed", ready=False),
        ]
        h = auth_headers(admin_user)
        body = (await client.get("/api/v1/containers/stats", headers=h)).json()
        assert body["in_use"] == body["total"] == 1
        assert [c["name"] for c in body["in_use_containers"]] == ["sinas-sbx-123"]
        assert body["finished"] == 2
        assert "2 finished sandbox pods" in body["note"]

        stats = (await client.get("/api/v1/queue/stats", headers=h)).json()
        assert stats["pool"] == {"idle": 0, "in_use": 1, "total": 1}

    async def test_unknown_sandbox_pods_stay_listed_not_finished(self, client, admin_user, k8s_mode):
        """Phase Unknown (node not reporting) hasn't been confirmed stopped:
        the pod stays visible, counted apart from both busy and finished."""
        k8s_mode.core.pods = RELEASE_PODS + [
            _pod("sinas-sbx-lost", sinas_type="sandbox-executor", phase="Unknown", ready=False),
            _pod("sinas-sbx-done", sinas_type="sandbox-executor", phase="Succeeded", ready=False),
        ]
        body = (await client.get("/api/v1/containers/stats", headers=auth_headers(admin_user))).json()
        assert body["in_use"] == body["total"] == 1
        assert body["unknown"] == 1 and body["finished"] == 1
        names = {c["name"]: c["status"] for c in body["in_use_containers"]}
        assert names == {"sinas-sbx-123": "pending", "sinas-sbx-lost": "unknown"}
        assert "unknown state" in body["note"] and "1 finished sandbox pod " in body["note"]

    async def test_shared_card_lists_trusted_pods(self, client, admin_user, k8s_mode):
        h = auth_headers(admin_user)
        workers = (await client.get("/api/v1/workers", headers=h)).json()
        assert workers == [
            {
                "id": "worker-0",
                "container_name": "rel-trusted-worker-0",
                "status": "running",
                "created_at": workers[0]["created_at"],
                "executions": 0,
            }
        ]
        count = (await client.get("/api/v1/workers/count", headers=h)).json()
        assert count["count"] == 1
        assert count["executor"] == "k8s_shared" and count["scalable"] is False

    async def test_queue_stats_use_k8s_sources(self, client, admin_user, k8s_mode):
        resp = await client.get("/api/v1/queue/stats", headers=auth_headers(admin_user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["pool"] == {"idle": 0, "in_use": 1, "total": 1}
        assert body["workers"] == {"count": 1, "running": 1}

    async def test_scaling_rejected_clearly(self, client, admin_user, k8s_mode):
        h = auth_headers(admin_user)
        r1 = await client.post("/api/v1/containers/scale", json={"target": 3}, headers=h)
        r2 = await client.post("/api/v1/workers/scale", json={"target_count": 3}, headers=h)
        assert r1.status_code == 409 and "k8s_pod" in r1.json()["detail"]
        assert r2.status_code == 409 and "k8s_shared" in r2.json()["detail"]

    async def test_inprocess_and_disabled_say_why(self, client, admin_user, k8s_mode, monkeypatch):
        h = auth_headers(admin_user)
        monkeypatch.setattr(settings, "trusted_executor", "inprocess")
        count = (await client.get("/api/v1/workers/count", headers=h)).json()
        assert count["count"] == 0 and "inprocess" in count["note"]

        monkeypatch.setattr(settings, "sandbox_executor", "disabled")
        stats = (await client.get("/api/v1/containers/stats", headers=h)).json()
        assert stats["total"] == 0 and "disabled" in stats["unavailable_reason"]


# ── Docker: unchanged ────────────────────────────────────────────────


class FakeContainer:
    def __init__(self, name, service=None, status="running"):
        self.name = name
        self.status = status
        self.labels = {"com.docker.compose.service": service} if service else {}
        self.attrs = {
            "State": {
                "Health": {"Status": "healthy"},
                "StartedAt": "2026-01-01T00:00:00.123456789Z",
            }
        }

    def stats(self, stream=False):
        return {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 300},
                "system_cpu_usage": 2000,
                "online_cpus": 2,
            },
            "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
            "memory_stats": {
                "usage": 300 * 1024 * 1024,
                "limit": 1024 * 1024 * 1024,
                "stats": {"cache": 44 * 1024 * 1024},
            },
        }


class FakeDocker:
    def __init__(self, containers):
        self.containers = SimpleNamespace(list=lambda **kw: containers)


class TestDockerHealth:
    async def test_compose_containers_and_stats(self, client, admin_user, docker_mode, monkeypatch):
        containers = [
            FakeContainer("sinas-backend-1", "backend"),
            FakeContainer("sinas-redis-1", "redis"),
            FakeContainer("sinas-queue-worker-1", "queue-worker", status="exited"),
            FakeContainer("sinas-shared-1", "executor"),
            FakeContainer("sinas-executor-1", "executor", status="exited"),  # builder: skipped
        ]
        monkeypatch.setattr(system_endpoint.docker, "from_env", lambda: FakeDocker(containers))
        body = await _health(client, admin_user)

        assert body["deployment"] == "docker"
        assert body["services_unavailable_reason"] is None
        assert body["metrics_unavailable_reason"] is None
        names = [s["name"] for s in body["services"]]
        assert names == [
            "sinas-redis-1",
            "sinas-backend-1",
            "sinas-queue-worker-1",
            "sinas-shared-1",
        ]
        by_name = {s["name"]: s for s in body["services"]}
        assert by_name["sinas-shared-1"]["service"] == "shared"
        backend = by_name["sinas-backend-1"]
        assert backend["health"] == "healthy" and backend["uptime_seconds"] > 0
        assert backend["cpu_percent"] == 40.0
        assert backend["memory"] == {"used_mb": 256.0, "limit_mb": 1024.0, "percent": 25.0}
        assert "cpu_percent" not in by_name["sinas-queue-worker-1"]  # not running
        assert any("queue-worker" in w["message"] for w in body["warnings"])

    async def test_docker_unreachable_degrades(self, client, admin_user, docker_mode, monkeypatch):
        def no_socket():
            raise docker.errors.DockerException("socket missing")

        monkeypatch.setattr(system_endpoint.docker, "from_env", no_socket)
        body = await _health(client, admin_user)
        assert body["services"] == []
        assert "Docker socket" in body["services_unavailable_reason"]

        resp = await client.post(
            "/api/v1/system/containers/sinas-backend-1/restart", headers=auth_headers(admin_user)
        )
        assert resp.status_code == 503

    async def test_docker_cards_pass_through(self, client, admin_user, docker_mode, monkeypatch):
        from app.services.container_pool import container_pool
        from app.services.shared_worker_manager import shared_worker_manager

        pool = {
            "idle": 2,
            "in_use": 1,
            "total": 3,
            "max_size": 10,
            "min_idle": 2,
            "max_executions": 100,
            "idle_containers": [],
            "in_use_containers": [],
        }
        worker = {
            "id": "worker-1",
            "container_name": "sinas-shared-1",
            "status": "running",
            "created_at": "2026-01-01T00:00:00Z",
            "executions": 4,
        }

        async def list_workers():
            return [worker]

        monkeypatch.setattr(container_pool, "get_stats", lambda: dict(pool))
        monkeypatch.setattr(shared_worker_manager, "list_workers", list_workers)
        monkeypatch.setattr(shared_worker_manager, "get_worker_count", lambda: 1)

        h = auth_headers(admin_user)
        stats = (await client.get("/api/v1/containers/stats", headers=h)).json()
        assert {k: stats[k] for k in pool} == pool
        assert stats["executor"] == "docker_pool" and stats["scalable"] is True

        assert (await client.get("/api/v1/workers", headers=h)).json() == [worker]
        count = (await client.get("/api/v1/workers/count", headers=h)).json()
        assert count["count"] == 1 and count["scalable"] is True
