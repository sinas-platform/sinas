"""Unit tests for the k8s sandbox runtime's pure pieces.

Pod manifest hardening and quantity conversion are contract-bearing (they are
the k8s translation of the Docker sandbox hardening in
`_ephemeral_runtime._run_config`); the API-touching lifecycle is exercised
against a real cluster in integration testing.
"""

import pytest

from app.services.executor._k8s_runtime import _k8s_quantity, _pod_manifest


def test_quantity_conversion():
    assert _k8s_quantity("1g") == "1Gi"
    assert _k8s_quantity("500m") == "500Mi"
    assert _k8s_quantity("64k") == "64Ki"
    assert _k8s_quantity("2Gi") == "2Gi"  # already a k8s quantity: passthrough


def test_pod_manifest_hardening():
    m = _pod_manifest("sinas-sbx-e1", "executor:latest", deadline_seconds=600)
    spec = m["spec"]
    assert spec["restartPolicy"] == "Never"
    assert spec["automountServiceAccountToken"] is False
    assert spec["activeDeadlineSeconds"] == 600

    c = spec["containers"][0]
    _assert_restricted(spec)
    # The executor image's non-root user (uid 10001, group 0) by default.
    assert spec["securityContext"]["runAsUser"] == 10001
    assert spec["securityContext"]["runAsGroup"] == 0
    assert spec["securityContext"]["fsGroup"] == 0

    # Sandbox mode must be set — it is what hard-disables input() in the
    # in-container executor.
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["SINAS_CONTAINER_MODE"] == "sandbox"

    # tmpfs /tmp parity with the Docker sandbox.
    assert spec["volumes"][0]["emptyDir"]["medium"] == "Memory"
    assert c["volumeMounts"][0]["mountPath"] == "/tmp"

    # Resource ceilings present.
    limits = c["resources"]["limits"]
    assert "memory" in limits and "cpu" in limits and "ephemeral-storage" in limits

    # Selectable by the chart's sandbox NetworkPolicy.
    assert m["metadata"]["labels"]["sinas.type"] == "sandbox-executor"


def test_pod_manifest_no_service_account_by_default():
    m = _pod_manifest("n", "img", deadline_seconds=1)
    assert "serviceAccountName" not in m["spec"]


# --- Runtime-pod placement and image -----------------------------------------
# Pods the platform creates at runtime (single-use sandbox pods and the warm
# trusted-worker pods) must follow the chart's scheduling and take the
# configured image reference verbatim (a digest-pinned ref must not be
# rewritten), or a cluster that only admits allowed node pools or
# digest-referenced images rejects them.

_SCHED = {
    "k8s_sandbox_node_selector": '{"pool": "restricted"}',
    "k8s_sandbox_tolerations": (
        '[{"key": "dedicated", "operator": "Equal", "value": "x", "effect": "NoSchedule"}]'
    ),
    "k8s_sandbox_affinity": (
        '{"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": '
        '{"nodeSelectorTerms": [{"matchExpressions": '
        '[{"key": "pool", "operator": "In", "values": ["restricted"]}]}]}}}'
    ),
}
_DIGEST_REF = "mirror.example.com/sinas/executor:0.5.0@sha256:" + "a" * 64


def _set_sched(monkeypatch):
    from app.core.config import settings

    for key, value in _SCHED.items():
        monkeypatch.setattr(settings, key, value)


def _assert_sched(spec):
    assert spec["nodeSelector"] == {"pool": "restricted"}
    assert spec["tolerations"][0]["key"] == "dedicated"
    assert "nodeAffinity" in spec["affinity"]


def test_sandbox_pod_gets_scheduling_and_digest_image(monkeypatch):
    _set_sched(monkeypatch)
    m = _pod_manifest("n", _DIGEST_REF, deadline_seconds=1)
    _assert_sched(m["spec"])
    assert m["spec"]["containers"][0]["image"] == _DIGEST_REF


def test_trusted_worker_pod_gets_scheduling_and_digest_image(monkeypatch):
    from app.services.executor.k8s_shared_trusted import _manifest

    _set_sched(monkeypatch)
    m = _manifest("sinas-trusted-worker-0", _DIGEST_REF, "0")
    _assert_sched(m["spec"])
    assert m["spec"]["containers"][0]["image"] == _DIGEST_REF


def test_runtime_pods_unconstrained_by_default():
    from app.services.executor.k8s_shared_trusted import _manifest

    for spec in (
        _pod_manifest("n", "img", deadline_seconds=1)["spec"],
        _manifest("w-0", "img", "0")["spec"],
    ):
        assert not {"nodeSelector", "tolerations", "affinity"} & spec.keys()


# --- Pod Security Standard "restricted" ----------------------------------------
# A namespace labelled pod-security.kubernetes.io/enforce=restricted refuses
# any pod that runs as root, may escalate privileges, keeps or adds a
# capability (other than NET_BIND_SERVICE), or has no seccomp profile. The
# pods the backend creates at runtime must pass it, or code execution breaks
# on such a cluster. The restricted rules, checked the way admission does:


def _assert_restricted(spec):
    pod_sc = spec.get("securityContext") or {}
    for c in spec.get("initContainers", []) + spec["containers"]:
        sc = c.get("securityContext") or {}
        # runAsNonRoot: true at pod or container level, never disabled
        assert sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot")) is True
        # runAsUser, when set, is not 0
        assert sc.get("runAsUser", pod_sc.get("runAsUser")) != 0
        # seccomp RuntimeDefault or Localhost at pod or container level
        profile = sc.get("seccompProfile") or pod_sc.get("seccompProfile") or {}
        assert profile.get("type") in ("RuntimeDefault", "Localhost")
        assert sc.get("allowPrivilegeEscalation") is False
        assert not sc.get("privileged")
        caps = sc.get("capabilities") or {}
        assert "ALL" in caps.get("drop", [])
        assert set(caps.get("add", [])) <= {"NET_BIND_SERVICE"}
    assert not (spec.get("hostNetwork") or spec.get("hostPID") or spec.get("hostIPC"))
    for v in spec.get("volumes", []):
        assert "hostPath" not in v


def _runtime_specs():
    from app.services.executor.k8s_shared_trusted import _manifest

    return (
        _pod_manifest("n", "img", deadline_seconds=1)["spec"],
        _manifest("w-0", "img", "0")["spec"],
    )


def test_runtime_pods_are_restricted_by_default():
    for spec in _runtime_specs():
        _assert_restricted(spec)
        # No root-only capabilities are added back (they were before 0.5.0).
        assert "add" not in spec["containers"][0]["securityContext"]["capabilities"]


def test_runtime_pod_identity_is_configurable(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "k8s_sandbox_pod_security_context",
        '{"runAsUser": 1000700000, "runAsGroup": 0, "fsGroup": 1000700000,'
        ' "supplementalGroups": [5]}',
    )
    for spec in _runtime_specs():
        sc = spec["securityContext"]
        assert sc["runAsUser"] == 1000700000
        assert sc["fsGroup"] == 1000700000
        assert sc["supplementalGroups"] == [5]
        # The floor is applied even when the configured value omits it.
        assert sc["runAsNonRoot"] is True
        assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
        _assert_restricted(spec)


def test_runtime_pod_null_identity_lets_the_platform_pick(monkeypatch):
    # OpenShift-style: no uid/gid in the spec, the SCC assigns them.
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "k8s_sandbox_pod_security_context",
        '{"runAsNonRoot": true, "runAsUser": null, "runAsGroup": null,'
        ' "fsGroup": null, "seccompProfile": {"type": "RuntimeDefault"}}',
    )
    for spec in _runtime_specs():
        sc = spec["securityContext"]
        assert not {"runAsUser", "runAsGroup", "fsGroup"} & sc.keys()
        _assert_restricted(spec)


def test_runtime_pod_container_floor_cannot_be_relaxed(monkeypatch):
    # Even a value that bypassed config validation (assigned at runtime)
    # cannot re-add capabilities or privilege escalation.
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "k8s_sandbox_container_security_context",
        '{"allowPrivilegeEscalation": true, "privileged": true,'
        ' "capabilities": {"add": ["SYS_ADMIN"]}, "readOnlyRootFilesystem": false}',
    )
    for spec in _runtime_specs():
        sc = spec["containers"][0]["securityContext"]
        assert sc["capabilities"] == {"drop": ["ALL"]}
        assert sc["allowPrivilegeEscalation"] is False
        assert "privileged" not in sc
        assert sc["readOnlyRootFilesystem"] is False  # other fields pass through
        _assert_restricted(spec)


def test_runtime_pod_seccomp_floor_cannot_be_relaxed(monkeypatch):
    # Unconfined at pod or container level (values that bypassed config
    # validation) never reaches the pod: the pod keeps RuntimeDefault and
    # the container drops its override, so the pod-level profile applies.
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "k8s_sandbox_pod_security_context",
        '{"runAsUser": 10001, "seccompProfile": {"type": "Unconfined"}}',
    )
    monkeypatch.setattr(
        settings,
        "k8s_sandbox_container_security_context",
        '{"seccompProfile": {"type": "Unconfined"}}',
    )
    for spec in _runtime_specs():
        assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        assert "seccompProfile" not in spec["containers"][0]["securityContext"]
        _assert_restricted(spec)


def test_runtime_pod_localhost_seccomp_is_kept(monkeypatch):
    from app.core.config import settings

    profile = {"type": "Localhost", "localhostProfile": "profiles/sinas.json"}
    monkeypatch.setattr(
        settings,
        "k8s_sandbox_pod_security_context",
        '{"seccompProfile": {"type": "Localhost", "localhostProfile": "profiles/sinas.json"}}',
    )
    monkeypatch.setattr(
        settings,
        "k8s_sandbox_container_security_context",
        '{"seccompProfile": {"type": "Localhost", "localhostProfile": "profiles/sinas.json"}}',
    )
    for spec in _runtime_specs():
        assert spec["securityContext"]["seccompProfile"] == profile
        assert spec["containers"][0]["securityContext"]["seccompProfile"] == profile
        _assert_restricted(spec)


@pytest.mark.parametrize(
    "field,value",
    [
        ("k8s_sandbox_pod_security_context", '{"runAsUser": 0}'),
        ("k8s_sandbox_pod_security_context", '{"runAsNonRoot": false}'),
        ("k8s_sandbox_pod_security_context", "[]"),
        ("k8s_sandbox_pod_security_context", "not json"),
        ("k8s_sandbox_container_security_context", '{"privileged": true}'),
        ("k8s_sandbox_container_security_context", '{"allowPrivilegeEscalation": true}'),
        ("k8s_sandbox_container_security_context", '{"capabilities": {"add": ["NET_RAW"]}}'),
        ("k8s_sandbox_container_security_context", '{"runAsUser": 0}'),
        ("k8s_sandbox_pod_security_context", '{"seccompProfile": {"type": "Unconfined"}}'),
        ("k8s_sandbox_pod_security_context", '{"seccompProfile": {}}'),
        ("k8s_sandbox_container_security_context", '{"seccompProfile": {"type": "Unconfined"}}'),
    ],
)
def test_security_context_settings_reject_relaxing_values(monkeypatch, field, value):
    from pydantic import ValidationError

    from app.core.config import Settings

    monkeypatch.setenv(field.upper(), value)
    with pytest.raises(ValidationError):
        Settings()


def test_security_context_settings_accept_chart_values(monkeypatch):
    # What the chart renders by default, and with the identity left to the
    # platform (runAsUser: null in values -> key absent).
    from app.core.config import Settings

    monkeypatch.setenv(
        "K8S_SANDBOX_POD_SECURITY_CONTEXT",
        '{"fsGroup":0,"fsGroupChangePolicy":"OnRootMismatch","runAsGroup":0,'
        '"runAsNonRoot":true,"runAsUser":10001,"seccompProfile":{"type":"RuntimeDefault"}}',
    )
    monkeypatch.setenv(
        "K8S_SANDBOX_CONTAINER_SECURITY_CONTEXT",
        '{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}',
    )
    Settings()
    monkeypatch.setenv(
        "K8S_SANDBOX_POD_SECURITY_CONTEXT",
        '{"runAsNonRoot":true,"seccompProfile":{"type":"RuntimeDefault"}}',
    )
    Settings()
