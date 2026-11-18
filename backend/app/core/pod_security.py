"""The Kubernetes Pod Security Standard "restricted", for the securityContext
of the pods the backend creates (sandbox + warm trusted workers).

`*_violations` list what a configured context would break (used to refuse
it at startup, in app.core.config); `harden_*` apply the floor to whatever
is configured (used when building the pods, in
app.services.executor._k8s_runtime), so a value that slipped past
validation still cannot relax it.

https://kubernetes.io/docs/concepts/security/pod-security-standards/
"""

from __future__ import annotations

from typing import Any

SAFE_SYSCTLS = frozenset({
    "kernel.shm_rmid_forced",
    "net.ipv4.ip_local_port_range",
    "net.ipv4.ip_unprivileged_port_start",
    "net.ipv4.tcp_syncookies",
    "net.ipv4.ping_group_range",
    "net.ipv4.ip_local_reserved_ports",
    "net.ipv4.tcp_keepalive_time",
    "net.ipv4.tcp_fin_timeout",
    "net.ipv4.tcp_keepalive_intvl",
    "net.ipv4.tcp_keepalive_probes",
})
ALLOWED_SELINUX_TYPES = frozenset({
    "", "container_t", "container_init_t", "container_kvm_t", "container_engine_t",
})
ALLOWED_CAP_ADD = frozenset({"NET_BIND_SERVICE"})
_PROFILE_TYPES = ("RuntimeDefault", "Localhost")


def profile_ok(profile: Any) -> bool:
    """A seccomp or AppArmor profile restricted accepts: RuntimeDefault, or
    Localhost naming its profile."""
    if not isinstance(profile, dict):
        return False
    kind = profile.get("type")
    if kind == "RuntimeDefault":
        return True
    return kind == "Localhost" and isinstance(profile.get("localhostProfile"), str) and bool(
        profile["localhostProfile"]
    )


def _selinux_ok(opts: Any) -> bool:
    if not isinstance(opts, dict):
        return False
    return (opts.get("type") or "") in ALLOWED_SELINUX_TYPES and not opts.get("user") and not opts.get(
        "role"
    )


def _common_violations(sc: dict) -> list[str]:
    out = []
    sc = {k: v for k, v in sc.items() if v is not None}  # null = unset
    if sc.get("runAsNonRoot") is False:
        out.append("runAsNonRoot: false")
    if sc.get("runAsUser") == 0:
        out.append("runAsUser: 0")
    if "seccompProfile" in sc and not profile_ok(sc["seccompProfile"]):
        out.append("seccompProfile (RuntimeDefault, or Localhost with localhostProfile)")
    if "appArmorProfile" in sc and not profile_ok(sc["appArmorProfile"]):
        out.append("appArmorProfile (RuntimeDefault, or Localhost with localhostProfile)")
    if "seLinuxOptions" in sc and not _selinux_ok(sc["seLinuxOptions"]):
        out.append("seLinuxOptions (no user/role, type one of the container_* types)")
    if (sc.get("windowsOptions") or {}).get("hostProcess"):
        out.append("windowsOptions.hostProcess")
    return out


def pod_violations(sc: dict) -> list[str]:
    out = _common_violations(sc)
    unsafe = [s.get("name") for s in sc.get("sysctls") or [] if s.get("name") not in SAFE_SYSCTLS]
    if unsafe:
        out.append(f"sysctls {unsafe}")
    return out


def container_violations(sc: dict) -> list[str]:
    out = _common_violations(sc)
    sc = {k: v for k, v in sc.items() if v is not None}
    if sc.get("privileged"):
        out.append("privileged")
    if sc.get("allowPrivilegeEscalation"):
        out.append("allowPrivilegeEscalation")
    extra = set((sc.get("capabilities") or {}).get("add") or []) - ALLOWED_CAP_ADD
    if extra:
        out.append(f"capabilities.add {sorted(extra)}")
    if sc.get("procMount") not in (None, "Default"):
        out.append(f"procMount {sc.get('procMount')}")
    return out


def _harden_common(sc: dict) -> dict:
    sc = {k: v for k, v in sc.items() if v is not None}
    for key, ok in (("appArmorProfile", profile_ok), ("seLinuxOptions", _selinux_ok)):
        if key in sc and not ok(sc[key]):
            del sc[key]
    if (sc.get("windowsOptions") or {}).get("hostProcess"):
        del sc["windowsOptions"]
    if sc.get("runAsUser") == 0:
        del sc["runAsUser"]
    return sc


def harden_pod(sc: dict) -> dict:
    """Pod-level floor: non-root, an allowed seccomp profile
    (RuntimeDefault unless a valid one is configured), safe sysctls only."""
    sc = _harden_common(sc)
    sc["runAsNonRoot"] = True
    if not profile_ok(sc.get("seccompProfile")):
        sc["seccompProfile"] = {"type": "RuntimeDefault"}
    if "sysctls" in sc:
        sc["sysctls"] = [s for s in sc["sysctls"] or [] if s.get("name") in SAFE_SYSCTLS]
        if not sc["sysctls"]:
            del sc["sysctls"]
    return sc


def harden_container(sc: dict) -> dict:
    """Container-level floor: no privilege escalation, every capability
    dropped (NET_BIND_SERVICE may be added back), default procMount. A
    container seccomp profile overrides the pod's, so a disallowed one is
    dropped and the pod's applies."""
    sc = _harden_common(sc)
    if "seccompProfile" in sc and not profile_ok(sc["seccompProfile"]):
        del sc["seccompProfile"]
    sc.pop("privileged", None)
    sc["allowPrivilegeEscalation"] = False
    if sc.get("runAsNonRoot") is False:
        del sc["runAsNonRoot"]
    if sc.get("procMount") not in (None, "Default"):
        del sc["procMount"]
    add = [c for c in (sc.get("capabilities") or {}).get("add") or [] if c in ALLOWED_CAP_ADD]
    sc["capabilities"] = {"drop": ["ALL"], **({"add": add} if add else {})}
    return sc
