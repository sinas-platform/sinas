"""The System page's Kubernetes calls behind a circuit breaker.

An apiserver the backend can't use (NetworkPolicy dropping its traffic, no
RBAC, no token) must cost one bounded call per backoff window, however many
tabs poll: after a failure the cached reason comes back at once, concurrent
callers share one thread, and nobody waits longer than _WAIT_SECONDS.
"""

import asyncio
import threading
import time

import pytest
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError

from app.core.config import settings
from app.services import k8s_stats
from app.services.k8s_stats import K8sUnavailableError, _Breaker

NS = "sinas-test"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeCore:
    """list_namespaced_pod that fails with `error` or sleeps `delay`."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.delay = 0.0
        self.calls = 0
        self._lock = threading.Lock()

    def list_namespaced_pod(self, namespace, label_selector="", _request_timeout=None):
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return type("R", (), {"items": []})()


class FakeCustom:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls = 0

    def list_namespaced_custom_object(self, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return {"items": []}


@pytest.fixture
def api(monkeypatch):
    k8s_stats.reset_breaker()
    clock = Clock()
    monkeypatch.setattr(k8s_stats._breaker, "_clock", clock)
    monkeypatch.setattr(settings, "k8s_release_name", "rel")
    monkeypatch.setattr(settings, "k8s_sandbox_namespace", "")
    monkeypatch.setenv("POD_NAMESPACE", NS)
    core, custom = FakeCore(), FakeCustom()
    monkeypatch.setattr(k8s_stats, "_apis", lambda: (core, custom))
    yield type("Api", (), {"core": core, "custom": custom, "clock": clock})
    k8s_stats.reset_breaker()


def _timeout() -> Exception:
    return MaxRetryError(None, "/api/v1/namespaces/x/pods", ConnectTimeoutError("connect timed out"))


# ── _Breaker ─────────────────────────────────────────────────────────


class TestBreaker:
    def test_failure_cached_then_recovers_after_window(self):
        clock = Clock()
        b = _Breaker(clock)
        b.check("k")  # closed
        assert b.failure("k", "down") == k8s_stats.BACKOFF_INITIAL_SECONDS
        with pytest.raises(K8sUnavailableError) as exc:
            b.check("k")
        assert str(exc.value) == "down" and exc.value.retry_after == 60
        clock.now += 59
        with pytest.raises(K8sUnavailableError) as exc:
            b.check("k")
        assert exc.value.retry_after == pytest.approx(1)
        clock.now += 1
        b.check("k")  # window over: the next call may try

    def test_window_doubles_up_to_the_cap_and_resets_on_success(self):
        clock = Clock()
        b = _Breaker(clock)
        windows = []
        for _ in range(5):
            windows.append(b.failure("k", "down"))
            clock.now += windows[-1]
        assert windows == [60, 120, 240, 300, 300]
        b.success("k")
        assert b.failure("k", "down") == 60

    def test_endless_failures_stay_at_the_cap(self):
        """A cluster without metrics-server fails forever: thousands of
        consecutive windows must not overflow the backoff arithmetic."""
        clock = Clock()
        b = _Breaker(clock)
        for _ in range(3000):
            window = b.failure("k", "down")
            clock.now += window
        assert window == k8s_stats.BACKOFF_MAX_SECONDS

    def test_failures_within_a_window_dont_lengthen_it(self):
        """Calls in flight together all fail: one window, not three doublings."""
        clock = Clock()
        b = _Breaker(clock)
        assert b.failure("*", "a") == 60
        clock.now += 1
        assert b.failure("*", "b") == 59
        clock.now += 59
        assert b.failure("*", "c") == 120  # a new, consecutive window

    def test_all_key_blocks_every_call_key_only_its_own(self):
        b = _Breaker(Clock())
        b.failure("metrics", "forbidden")
        b.check("pods")
        with pytest.raises(K8sUnavailableError):
            b.check("metrics")
        b.failure("*", "unreachable")
        with pytest.raises(K8sUnavailableError, match="unreachable"):
            b.check("pods")


# ── Calls through the breaker ────────────────────────────────────────


class TestCalls:
    async def test_timeout_cached_no_second_call_within_window(self, api):
        api.core.error = _timeout()
        with pytest.raises(K8sUnavailableError) as exc:
            await k8s_stats.list_executor_pods(k8s_stats.SANDBOX_POD_TYPE)
        assert "did not answer in time" in str(exc.value)
        assert "NetworkPolicy" in str(exc.value)
        assert exc.value.retry_after == 60
        assert api.core.calls == 1

        # Every caller (services list, both executor cards) gets the cached
        # reason at once: a transport failure opens the breaker for all.
        for _ in range(5):
            services, unavailable, _ = await k8s_stats.list_services(include_stats=True)
            assert services == [] and "did not answer in time" in str(unavailable)
            with pytest.raises(K8sUnavailableError):
                await k8s_stats.list_executor_pods(k8s_stats.TRUSTED_POD_TYPE)
        assert api.core.calls == 1 and api.custom.calls == 0

    async def test_recovers_after_window(self, api):
        api.core.error = ConnectionRefusedError("refused")
        _, unavailable, _ = await k8s_stats.list_services(include_stats=False)
        assert "not reachable" in str(unavailable) and "ConnectionRefusedError" in str(unavailable)

        api.core.error = None
        api.clock.now += 59
        _, unavailable, _ = await k8s_stats.list_services(include_stats=False)
        assert unavailable is not None and api.core.calls == 1

        api.clock.now += 1
        services, unavailable, _ = await k8s_stats.list_services(include_stats=False)
        assert unavailable is None and services == []
        assert api.core.calls == 2
        # closed again: every call goes through
        await k8s_stats.list_services(include_stats=False)
        assert api.core.calls == 3

    async def test_forbidden_reason_is_clear_and_per_call(self, api):
        """403 on metrics (no metrics RBAC) must not hide the pod list."""
        api.custom.error = ApiException(status=403, reason="Forbidden")
        services, unavailable, metrics_reason = await k8s_stats.list_services(include_stats=True)
        assert unavailable is None
        assert "isn't allowed to read pod metrics" in metrics_reason
        assert NS in metrics_reason and "Traceback" not in metrics_reason

        await k8s_stats.list_services(include_stats=True)
        assert api.core.calls == 2 and api.custom.calls == 1  # metrics cached

        api.core.error = ApiException(status=403, reason="Forbidden")
        with pytest.raises(K8sUnavailableError) as exc:
            await k8s_stats.list_executor_pods(k8s_stats.SANDBOX_POD_TYPE)
        assert "the backend's service account isn't allowed to list pods" in str(exc.value).lower()

    async def test_unauthorized_and_no_credentials(self, api):
        api.core.error = ApiException(status=401, reason="Unauthorized")
        _, unavailable, _ = await k8s_stats.list_services(include_stats=False)
        assert "rejected the backend's credentials" in str(unavailable)
        # 401 is about every call
        with pytest.raises(K8sUnavailableError):
            await k8s_stats.list_executor_pods(k8s_stats.SANDBOX_POD_TYPE)
        assert api.core.calls == 1

        k8s_stats.reset_breaker()
        api.core.error = ConfigException("Service host/port is not set.")
        _, unavailable, _ = await k8s_stats.list_services(include_stats=False)
        assert "no Kubernetes API credentials" in str(unavailable)

    async def test_concurrent_callers_share_one_thread(self, api):
        api.core.delay = 0.3
        results = await asyncio.gather(
            *[k8s_stats.list_executor_pods(k8s_stats.SANDBOX_POD_TYPE) for _ in range(10)]
        )
        assert results == [[]] * 10
        assert api.core.calls == 1

    async def test_blocked_apiserver_never_holds_a_request(self, api, monkeypatch):
        """The call outlives the wait budget: callers get a reason within it,
        later callers don't wait again, and once the thread fails (its own
        connect timeout) the breaker serves the reason."""
        monkeypatch.setattr(k8s_stats, "_WAIT_SECONDS", 0.2)
        api.core.delay = 0.6
        api.core.error = _timeout()

        start = time.monotonic()
        _, unavailable, _ = await k8s_stats.list_services(include_stats=False)
        assert time.monotonic() - start < 0.5
        assert "has not answered" in str(unavailable)

        await asyncio.sleep(0.25)  # past the budget, the call still running
        start = time.monotonic()
        _, unavailable, _ = await k8s_stats.list_services(include_stats=False)
        assert time.monotonic() - start < 0.1
        assert "has not answered" in str(unavailable)
        assert api.core.calls == 1

        await asyncio.sleep(0.4)  # the thread's call has failed by now
        _, unavailable, _ = await k8s_stats.list_services(include_stats=False)
        assert "did not answer in time" in str(unavailable)
        assert unavailable.retry_after is not None
        assert api.core.calls == 1

    async def test_missing_release_name_is_not_a_cluster_failure(self, api, monkeypatch):
        monkeypatch.setattr(settings, "k8s_release_name", "")
        _, unavailable, _ = await k8s_stats.list_services(include_stats=False)
        assert "K8S_RELEASE_NAME" in str(unavailable)
        assert unavailable.retry_after is None
        assert api.core.calls == 0


def test_stats_client_has_urllib3_retries_off(monkeypatch):
    """One attempt per call: urllib3's default would try a timed-out
    connection 4 times (4x the connect timeout)."""
    from kubernetes import client

    from app.services.executor import _k8s_runtime

    monkeypatch.setattr(k8s_stats, "_stats_apis", None)
    monkeypatch.setattr(_k8s_runtime, "_get_clients", lambda: None)
    cfg = client.Configuration()
    cfg.host = "https://10.0.0.1:443"
    monkeypatch.setattr(client.Configuration, "_default", cfg)

    core, custom = k8s_stats._apis()
    assert core.api_client.configuration.retries is False
    assert custom.api_client is core.api_client
    assert cfg.retries is None  # the executor's shared config is untouched
    assert k8s_stats._API_TIMEOUT == (2.0, 5.0)
