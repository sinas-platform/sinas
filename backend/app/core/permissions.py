"""Permission management utilities."""
from pathlib import Path
from typing import Set, Dict, List, Optional


def load_permissions() -> Set[str]:
    """Load all valid permission strings from permissions.txt file."""
    permissions_file = Path(__file__).parent / "permissions.txt"
    permissions = set()

    with open(permissions_file, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if line and not line.startswith('#'):
                permissions.add(line)

    return permissions


# Cache of all valid permissions
ALL_PERMISSIONS: Set[str] = load_permissions()


def expand_wildcard_permission(permission: str) -> Set[str]:
    """
    Expand a wildcard permission to all matching concrete permissions.

    Supports:
    - service.*:scope -> all permissions for that service and scope (matches any remaining parts)
    - service.resource.*:scope -> all actions for that resource and scope
    - scope can also be wildcard (*) or 'all' to match all scopes

    Args:
        permission: Permission string with potential wildcards

    Returns:
        Set of concrete permissions matching the wildcard
    """
    if '*' not in permission:
        return {permission} if permission in ALL_PERMISSIONS else set()

    matching_permissions = set()

    # Parse the wildcard pattern
    try:
        parts, scope = permission.rsplit(':', 1)
        parts_list = parts.split('.')

        for perm in ALL_PERMISSIONS:
            try:
                perm_parts, perm_scope = perm.rsplit(':', 1)
            except ValueError:
                # Skip permissions without scope
                continue

            perm_parts_list = perm_parts.split('.')

            # Check scope with hierarchy: :all grants :group and :own
            scope_hierarchy = {
                'all': ['all', 'group', 'own'],
                'group': ['group', 'own'],
                'own': ['own'],
                '*': ['all', 'group', 'own']
            }
            allowed_scopes = scope_hierarchy.get(scope, [scope])
            if perm_scope not in allowed_scopes:
                continue

            # Check if pattern matches
            # If pattern ends with wildcard, only check the parts before it
            if parts_list[-1] == '*':
                # Pattern like "sinas.*" should match any permission starting with "sinas."
                # Compare only the non-wildcard parts
                prefix_parts = parts_list[:-1]
                if len(perm_parts_list) < len(prefix_parts):
                    continue

                matches = True
                for pattern_part, perm_part in zip(prefix_parts, perm_parts_list):
                    if pattern_part != perm_part:
                        matches = False
                        break

                if matches:
                    matching_permissions.add(perm)
            else:
                # Exact length match required
                if len(parts_list) != len(perm_parts_list):
                    continue

                matches = True
                for pattern_part, perm_part in zip(parts_list, perm_parts_list):
                    if pattern_part != '*' and pattern_part != perm_part:
                        matches = False
                        break

                if matches:
                    matching_permissions.add(perm)

    except ValueError as e:
        # Pattern doesn't have scope separator
        return set()

    return matching_permissions


def matches_permission_pattern(pattern: str, concrete: str) -> bool:
    """
    Check if a concrete permission matches a wildcard pattern.

    This is the inverse of expand_wildcard_permission - instead of expanding
    a pattern to find matches in a static list, this checks if a specific
    permission would match a pattern.

    Scope Hierarchy:
    - :all grants :group and :own
    - :group grants :own (future enhancement)
    - Pattern with :all matches requests for :group or :own

    Args:
        pattern: Permission pattern with potential wildcards (e.g., "sinas.ontology.*.create:group")
        concrete: Concrete permission to check (e.g., "sinas.ontology.concepts.create:group")

    Returns:
        True if concrete permission matches the pattern

    Examples:
        matches_permission_pattern("sinas.ontology.*:group", "sinas.ontology.concepts.create:group") -> True
        matches_permission_pattern("sinas.ontology.*.*.create:group", "sinas.ontology.concepts.commerce.create:group") -> True
        matches_permission_pattern("sinas.*.concepts.create:all", "sinas.ontology.concepts.create:group") -> True (all matches any scope)
        matches_permission_pattern("sinas.*:all", "sinas.functions.create:group") -> True (scope hierarchy)
    """
    # Split by scope separator
    try:
        pattern_parts, pattern_scope = pattern.rsplit(':', 1)
        concrete_parts, concrete_scope = concrete.rsplit(':', 1)
    except ValueError:
        return False

    # Check scope with hierarchy: :all grants :group and :own
    # Pattern scope '*' or 'all' matches any concrete scope
    # Pattern scope 'all' also matches requests for 'group' or 'own'
    scope_hierarchy = {
        'all': ['all', 'group', 'own'],
        'group': ['group', 'own'],
        'own': ['own'],
        '*': ['all', 'group', 'own']
    }

    allowed_scopes = scope_hierarchy.get(pattern_scope, [pattern_scope])
    if concrete_scope not in allowed_scopes:
        return False

    # Split by dots
    pattern_segments = pattern_parts.split('.')
    concrete_segments = concrete_parts.split('.')

    # If pattern ends with *, it can match any number of remaining segments
    if pattern_segments[-1] == '*':
        # Pattern like "sinas.ontology.*" should match "sinas.ontology.concepts.create"
        # Check that all non-wildcard prefix parts match
        prefix_segments = pattern_segments[:-1]
        if len(concrete_segments) < len(prefix_segments):
            return False

        for i, pattern_seg in enumerate(prefix_segments):
            if pattern_seg != '*' and pattern_seg != concrete_segments[i]:
                return False
        return True

    # Otherwise, exact length match required
    if len(pattern_segments) != len(concrete_segments):
        return False

    # Check each segment
    for i, (pattern_seg, concrete_seg) in enumerate(zip(pattern_segments, concrete_segments)):
        if pattern_seg != '*' and pattern_seg != concrete_seg:
            return False

    return True


def check_permission_with_wildcards(
    permissions: Dict[str, bool],
    required_permission: str
) -> bool:
    """
    Check if user has a permission, supporting wildcard matching and scope hierarchy.

    This checks if any of the user's permissions (which may contain wildcards)
    would grant access to the required concrete permission. Also checks if user
    has a higher scope permission (e.g., :all grants :group).

    Args:
        permissions: User's permission dictionary (may contain wildcards)
        required_permission: The concrete permission needed

    Returns:
        True if user has permission (directly, via wildcard, or via scope hierarchy), False otherwise

    Examples:
        User has: {"sinas.ontology.*.create:group": True}
        Checking: "sinas.ontology.concepts.create:group"
        Result: True (matches via wildcard)

        User has: {"sinas.chats.read:all": True}
        Checking: "sinas.chats.read:group"
        Result: True (matches via scope hierarchy)
    """
    # First check for exact match
    if permissions.get(required_permission):
        return True

    # Check all user permissions (wildcard AND non-wildcard) using pattern matching
    # This handles both wildcards and scope hierarchy
    for user_perm, has_perm in permissions.items():
        if has_perm and matches_permission_pattern(user_perm, required_permission):
            return True

    return False


def check_permission(
    permissions: Dict[str, bool],
    required_permission: str
) -> bool:
    """
    Generic permission checker that works for any permission string.

    Checks if user has the required permission either directly or via wildcard patterns.
    This works for ANY resource type: ontology, functions, webhooks, schedules, etc.

    Args:
        permissions: User's permission dictionary (may contain wildcards)
        required_permission: The concrete permission needed (e.g., "sinas.ontology.records.commerce.customer.create:group")

    Returns:
        True if user has permission (directly or via wildcard), False otherwise

    Examples:
        check_permission({"sinas.ontology.*.create:group": True}, "sinas.ontology.concepts.create:group") -> True
        check_permission({"sinas.functions.*.execute:own": True}, "sinas.functions.mygroup.myfunc.execute:own") -> True
    """
    return check_permission_with_wildcards(permissions, required_permission)


def check_ontology_permission(
    permissions: Dict[str, bool],
    resource: str,
    action: str,
    namespace: str,
    concept: Optional[str] = None,
    scope: str = "group"
) -> bool:
    """
    DEPRECATED: Use check_permission() with dynamically constructed permission strings instead.

    Check if user has permission for an ontology operation.
    This is a convenience wrapper that's kept for backwards compatibility.

    Checks permissions in order of specificity. Note that scope hierarchy (:all grants :group)
    is now handled automatically by check_permission(), so we only need to check the requested scope.

    - If concept provided:
      1. sinas.ontology.{resource}.{namespace}.{concept}.{action}:{scope}
      2. sinas.ontology.{resource}.{namespace}.*.{action}:{scope}
      3. sinas.ontology.{resource}.*.*.{action}:{scope}
      4. sinas.ontology.{resource}.*.{action}:{scope} (less specific wildcard)
    - If no concept:
      1. sinas.ontology.{resource}.{namespace}.{action}:{scope}
      2. sinas.ontology.{resource}.*.{action}:{scope}

    Args:
        permissions: User's permission dictionary
        resource: Ontology resource type (concepts, properties, queries, endpoints, records, etc.)
        action: Action to check (create, read, update, delete)
        namespace: Ontology namespace
        concept: Optional concept name
        scope: Permission scope (group, all)

    Returns:
        True if user has permission, False otherwise
    """
    if concept:
        # Check with concept (most specific first)
        # Scope hierarchy is handled automatically by check_permission()
        permission_checks = [
            f"sinas.ontology.{resource}.{namespace}.{concept}.{action}:{scope}",
            f"sinas.ontology.{resource}.{namespace}.*.{action}:{scope}",
            f"sinas.ontology.{resource}.*.*.{action}:{scope}",
            f"sinas.ontology.{resource}.*.{action}:{scope}",
        ]
    else:
        # Check without concept
        permission_checks = [
            f"sinas.ontology.{resource}.{namespace}.{action}:{scope}",
            f"sinas.ontology.{resource}.*.{action}:{scope}",
        ]

    for perm in permission_checks:
        if check_permission(permissions, perm):
            return True

    return False


def expand_permission_dict(permissions: Dict[str, bool]) -> Dict[str, bool]:
    """
    Expand a permission dictionary containing wildcards to concrete permissions.

    Args:
        permissions: Dict mapping permission strings to boolean values

    Returns:
        Dict with all wildcards expanded to concrete permissions
    """
    expanded = {}

    for perm, value in permissions.items():
        if '*' in perm:
            # Expand wildcard
            concrete_perms = expand_wildcard_permission(perm)
            for concrete_perm in concrete_perms:
                # Later definitions override earlier ones
                expanded[concrete_perm] = value
        else:
            # Direct permission
            if perm in ALL_PERMISSIONS:
                expanded[perm] = value

    return expanded


def validate_permission_subset(
    subset_perms: Dict[str, bool],
    superset_perms: Dict[str, bool]
) -> tuple[bool, List[str]]:
    """
    Validate that subset permissions are contained within superset permissions.

    Used for API key creation - ensures API keys can't have more permissions
    than the user's group permissions.

    Args:
        subset_perms: Permissions to validate
        superset_perms: Permissions that must contain the subset

    Returns:
        Tuple of (is_valid, list_of_violations)
    """
    # Expand wildcards in both
    subset_expanded = expand_permission_dict(subset_perms)
    superset_expanded = expand_permission_dict(superset_perms)

    violations = []

    for perm, value in subset_expanded.items():
        if value:  # Only check permissions that are granted (True)
            # Check if superset has this permission granted
            superset_value = superset_expanded.get(perm, False)
            if not superset_value:
                violations.append(perm)

    return len(violations) == 0, violations


# Default group permissions
DEFAULT_GROUP_PERMISSIONS = {
    "GuestUsers": {
        "sinas.*:own": False,  # No access by default
        "sinas.users.get:own": True,
        "sinas.users.put:own": True,
    },
    "Users": {
        # Chats
        "sinas.chats.post:own": True,
        "sinas.chats.get:own": True,
        "sinas.chats.get:group": True,
        "sinas.chats.put:own": True,
        "sinas.chats.delete:own": True,

        # Messages
        "sinas.messages.post:own": True,
        "sinas.messages.get:own": True,
        "sinas.messages.get:group": True,

        # Agents (namespace-based)
        "sinas.agents.*.post:own": True,
        "sinas.agents.*.get:own": True,
        "sinas.agents.*.put:own": True,
        "sinas.agents.*.delete:own": True,

        # Functions (namespace-based)
        "sinas.functions.*.post:own": True,
        "sinas.functions.*.get:own": True,
        "sinas.functions.*.put:own": True,
        "sinas.functions.*.delete:own": True,
        "sinas.functions.*.execute:own": True,

        # Webhooks
        "sinas.webhooks.post:own": True,
        "sinas.webhooks.get:own": True,
        "sinas.webhooks.put:own": True,
        "sinas.webhooks.delete:own": True,

        # Schedules
        "sinas.schedules.post:own": True,
        "sinas.schedules.get:own": True,
        "sinas.schedules.put:own": True,
        "sinas.schedules.delete:own": True,

        # Executions
        "sinas.executions.get:own": True,

        # Packages
        "sinas.packages.post:own": True,
        "sinas.packages.get:own": True,
        "sinas.packages.delete:own": True,

        # Users
        "sinas.users.get:own": True,
        "sinas.users.put:own": True,

        # API Keys
        "sinas.api_keys.post:own": True,
        "sinas.api_keys.get:own": True,
        "sinas.api_keys.delete:own": True,

        # State Store
        "sinas.states.post:own": True,
        "sinas.states.get:own": True,
        "sinas.states.get:group": True,
        "sinas.states.put:own": True,
        "sinas.states.delete:own": True,
        "sinas.states.search:own": True,

        # Request Logs
        "sinas.logs.get:own": True,
    },
    "Admins": {
        "sinas.*:all": True,  # Full access to everything
    }
}
