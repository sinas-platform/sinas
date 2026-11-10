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
    sc = c["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"]["drop"] == ["ALL"]
    assert set(sc["capabilities"]["add"]) == {"CHOWN", "SETUID", "SETGID"}
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}

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
