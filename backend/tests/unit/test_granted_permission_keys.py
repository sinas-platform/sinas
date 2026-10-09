"""Keys the console grants must satisfy the checks the runtime performs (#77).

An admin whose API key held `sinas.functions.execute:all` — the key the
console's permission editor produced — was refused by every resource-level
check, while the same person's console session worked. Resource endpoints
check a concrete path (`sinas.functions/acme/send.execute:own`), and a
pattern carrying no path never matches one that does.

The editor now grants both forms for a namespaced resource, because neither
alone covers a whole resource: most actions are checked against the path,
while `create` and `functions.shared_pool` are still checked flat. This test
encodes that contract in the backend, where the matcher lives — the console
helper `permissionKeysFor` in `console/src/components/PermissionEditor.tsx`
must keep producing what it asserts.
"""

import pytest

from app.core.permission_registry import PERMISSION_REGISTRY
from app.core.permissions import check_permission

NAMESPACED = [e for e in PERMISSION_REGISTRY if e.get("namespaced")]


def granted_keys(entry: dict, action: str, scope: str) -> dict[str, bool]:
    """Mirror of the console's `permissionKeysFor`."""
    keys = [f"sinas.{entry['resource']}.{action}:{scope}"]
    if entry.get("namespaced"):
        keys.append(f"sinas.{entry['resource']}/*/*.{action}:{scope}")
    return {key: True for key in keys}


class TestNamespacedGrants:
    @pytest.mark.parametrize("entry", NAMESPACED, ids=lambda e: e["resource"])
    def test_granting_an_action_covers_the_concrete_path_check(self, entry):
        for action in entry["actions"]:
            permissions = granted_keys(entry, action, "all")
            concrete = f"sinas.{entry['resource']}/acme/thing.{action}:own"
            assert check_permission(permissions, concrete), (
                f"granting {action} on {entry['resource']} does not satisfy {concrete}"
            )

    @pytest.mark.parametrize("entry", NAMESPACED, ids=lambda e: e["resource"])
    def test_granting_an_action_covers_the_flat_check(self, entry):
        """`create` and `functions.shared_pool` are checked without a path."""
        for action in entry["actions"]:
            permissions = granted_keys(entry, action, "all")
            flat = f"sinas.{entry['resource']}.{action}:own"
            assert check_permission(permissions, flat), (
                f"granting {action} on {entry['resource']} does not satisfy {flat}"
            )

    def test_the_flat_key_alone_is_what_used_to_fail(self):
        """Documents the bug: this is what the editor granted before."""
        flat_only = {"sinas.functions.execute:all": True}
        assert not check_permission(
            flat_only, "sinas.functions/acme/send_email.execute:own"
        )

    def test_grants_do_not_leak_across_resources_or_actions(self):
        permissions = granted_keys(
            {"resource": "functions", "namespaced": True}, "execute", "all"
        )
        assert not check_permission(permissions, "sinas.agents/acme/a.execute:own")
        assert not check_permission(permissions, "sinas.functions/acme/f.delete:own")

    def test_own_scope_does_not_grant_all(self):
        permissions = granted_keys(
            {"resource": "functions", "namespaced": True}, "execute", "own"
        )
        assert check_permission(permissions, "sinas.functions/acme/f.execute:own")
        assert not check_permission(permissions, "sinas.functions/acme/f.execute:all")
