#!/usr/bin/env python3
"""Static Pod Security Standard "restricted" check for rendered manifests.

Usage: check_pod_security.py RENDERED.yaml [RENDERED.yaml ...]

Checks every pod spec in the given `helm template` output (Pods, Deployments,
StatefulSets, DaemonSets, ReplicaSets, Jobs, CronJobs) against the rules the
Kubernetes admission controller applies at `enforce: restricted`, and the
securityContext the backend gives the pods it creates at runtime (sandbox +
warm trusted workers), which the chart passes as K8S_SANDBOX_POD_SECURITY_
CONTEXT / K8S_SANDBOX_CONTAINER_SECURITY_CONTEXT. Exits non-zero on any
violation, so a template change that would get a pod refused fails CI here,
before the kind job (.github/workflows/k8s-restricted.yml) proves it live.

https://kubernetes.io/docs/concepts/security/pod-security-standards/
"""

from __future__ import annotations

import json
import sys

import yaml

ALLOWED_VOLUMES = {
    "configMap", "csi", "downwardAPI", "emptyDir", "ephemeral",
    "persistentVolumeClaim", "projected", "secret",
}
SAFE_SYSCTLS = {
    "kernel.shm_rmid_forced", "net.ipv4.ip_local_port_range",
    "net.ipv4.ip_unprivileged_port_start", "net.ipv4.tcp_syncookies",
    "net.ipv4.ping_group_range", "net.ipv4.ip_local_reserved_ports",
    "net.ipv4.tcp_keepalive_time", "net.ipv4.tcp_fin_timeout",
    "net.ipv4.tcp_keepalive_intvl", "net.ipv4.tcp_keepalive_probes",
}
POD_KINDS = {"Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob"}


def pod_spec(doc: dict) -> dict | None:
    kind = doc.get("kind")
    if kind == "Pod":
        return doc.get("spec")
    if kind == "CronJob":
        return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    if kind in POD_KINDS:
        return doc["spec"]["template"]["spec"]
    return None


def violations(spec: dict) -> list[str]:
    out: list[str] = []
    psc = spec.get("securityContext") or {}
    containers = (
        [("initContainer", c) for c in spec.get("initContainers") or []]
        + [("container", c) for c in spec.get("containers") or []]
        + [("ephemeralContainer", c) for c in spec.get("ephemeralContainers") or []]
    )
    if not containers:
        out.append("no containers")

    # --- baseline ---
    for f in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(f):
            out.append(f"{f}: true")
    for s in psc.get("sysctls") or []:
        if s.get("name") not in SAFE_SYSCTLS:
            out.append(f"unsafe sysctl {s.get('name')}")
    if (psc.get("seccompProfile") or {}).get("type") == "Unconfined":
        out.append("pod seccompProfile Unconfined")
    if (psc.get("windowsOptions") or {}).get("hostProcess"):
        out.append("pod windowsOptions.hostProcess")

    # --- restricted: volumes ---
    for v in spec.get("volumes") or []:
        types = set(v) - {"name"}
        if not types <= ALLOWED_VOLUMES:
            out.append(f"volume {v.get('name')}: type {sorted(types - ALLOWED_VOLUMES)} not allowed")

    pod_nonroot = psc.get("runAsNonRoot")
    pod_seccomp = (psc.get("seccompProfile") or {}).get("type")
    if psc.get("runAsUser") == 0:
        out.append("pod runAsUser: 0")
    if pod_nonroot is False:
        out.append("pod runAsNonRoot: false")

    for role, c in containers:
        name = f"{role} {c.get('name')}"
        sc = c.get("securityContext") or {}
        if sc.get("privileged"):
            out.append(f"{name}: privileged")
        if (sc.get("windowsOptions") or {}).get("hostProcess"):
            out.append(f"{name}: windowsOptions.hostProcess")
        if sc.get("procMount") not in (None, "Default"):
            out.append(f"{name}: procMount {sc.get('procMount')}")
        for p in c.get("ports") or []:
            if p.get("hostPort"):
                out.append(f"{name}: hostPort {p['hostPort']}")
        if sc.get("allowPrivilegeEscalation") is not False:
            out.append(f"{name}: allowPrivilegeEscalation must be false")
        nonroot = sc.get("runAsNonRoot", pod_nonroot)
        if nonroot is not True:
            out.append(f"{name}: runAsNonRoot must be true (pod or container)")
        if sc.get("runAsUser") == 0:
            out.append(f"{name}: runAsUser: 0")
        seccomp = (sc.get("seccompProfile") or {}).get("type") or pod_seccomp
        if seccomp not in ("RuntimeDefault", "Localhost"):
            out.append(f"{name}: seccompProfile must be RuntimeDefault or Localhost (pod or container)")
        caps = sc.get("capabilities") or {}
        if "ALL" not in (caps.get("drop") or []):
            out.append(f"{name}: capabilities.drop must include ALL")
        extra = set(caps.get("add") or []) - {"NET_BIND_SERVICE"}
        if extra:
            out.append(f"{name}: capabilities.add {sorted(extra)} not allowed")
    return out


def runtime_pod_specs(spec: dict) -> list[tuple[str, dict]]:
    """Synthetic pod specs for the runtime pods a backend container creates,
    from its K8S_SANDBOX_*_SECURITY_CONTEXT env (checked as rendered, before
    the backend applies its own floor)."""
    found = []
    for c in spec.get("containers") or []:
        env = {e["name"]: e.get("value") for e in c.get("env") or [] if "name" in e}
        if "K8S_SANDBOX_POD_SECURITY_CONTEXT" not in env and "K8S_SANDBOX_IMAGE" not in env:
            continue
        pod_sc = env.get("K8S_SANDBOX_POD_SECURITY_CONTEXT")
        ctr_sc = env.get("K8S_SANDBOX_CONTAINER_SECURITY_CONTEXT")
        if pod_sc is None or ctr_sc is None:
            found.append((c.get("name"), None))
            continue
        found.append((c.get("name"), {
            "securityContext": json.loads(pod_sc),
            "containers": [{"name": "executor", "securityContext": json.loads(ctr_sc)}],
            "volumes": [{"name": "tmp", "emptyDir": {"medium": "Memory"}}],
        }))
    return found


def main(paths: list[str]) -> int:
    failed = 0
    checked = 0
    for path in paths:
        with open(path) as f:
            docs = [d for d in yaml.safe_load_all(f) if isinstance(d, dict)]
        print(f"== {path}")
        for doc in docs:
            spec = pod_spec(doc)
            if spec is None:
                continue
            checked += 1
            label = f"{doc['kind']}/{doc['metadata']['name']}"
            bad = violations(spec)
            for container, rspec in runtime_pod_specs(spec):
                if rspec is None:
                    bad.append(f"{container}: runtime-pod securityContext env missing")
                else:
                    bad += [f"runtime pods (via {container}): {v}" for v in violations(rspec)]
            if bad:
                failed += 1
                print(f"  FAIL {label}")
                for v in bad:
                    print(f"       - {v}")
            else:
                print(f"  ok   {label}")
    if not checked:
        print("no pod specs found")
        return 1
    print(f"{checked} pod spec(s) checked, {failed} failing")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
