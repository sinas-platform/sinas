"""Permission management utilities."""
from typing import Dict, List, Optional


def matches_permission_pattern(pattern: str, concrete: str) -> bool:
    """
    Check if a concrete permission matches a wildcard pattern.

    Uses pattern matching to determine if a user's wildcard permission (e.g., sinas.*:all)
    grants access to a specific requested permission (e.g., sinas.users.put:own).

    Scope Hierarchy:
    - :all grants :group and :own
    - :group grants :own (future enhancement)
    - Pattern with :all matches requests for :group or :own

    Args:
        pattern: Permission pattern with potential wildcards
        concrete: Concrete permission to check

    Returns:
        True if concrete permission matches the pattern

    Examples:
        # Trailing wildcard matches any suffix
        matches_permission_pattern("sinas.*:all", "sinas.users.put:own") -> True
        matches_permission_pattern("titan.*:group", "titan.content.get:group") -> True

        # Mid-pattern wildcards match specific segments
        matches_permission_pattern("sinas.*.get:own", "sinas.users.get:own") -> True
        matches_permission_pattern("sinas.*.get:own", "sinas.chats.get:own") -> True
        matches_permission_pattern("sinas.*.get:own", "sinas.users.post:own") -> False (action mismatch)

        # Scope hierarchy: :all grants lower scopes
        matches_permission_pattern("sinas.users.get:all", "sinas.users.get:group") -> True
        matches_permission_pattern("sinas.users.get:all", "sinas.users.get:own") -> True
        matches_permission_pattern("sinas.users.get:group", "sinas.users.get:all") -> False (can't elevate)
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


def check_permission(
    permissions: Dict[str, bool],
    required_permission: str
) -> bool:
    """
    Check if user has a permission, supporting wildcard matching and scope hierarchy.

    Checks if user has the required permission either directly, via wildcard patterns,
    or via scope hierarchy (e.g., :all grants :group and :own).

    Works for ANY resource type: sinas.*, custom namespaces (custom.*, acme.*), etc.

    Args:
        permissions: User's permission dictionary (may contain wildcards)
        required_permission: The concrete permission needed

    Returns:
        True if user has permission (directly, via wildcard, or via scope hierarchy), False otherwise

    Examples:
        # Exact match
        check_permission({"sinas.users.post:all": True}, "sinas.users.post:all") -> True

        # Wildcard matching - admin has full access
        check_permission({"sinas.*:all": True}, "sinas.users.post:own") -> True
        check_permission({"sinas.*:all": True}, "sinas.chats.get:group") -> True

        # Namespace-based wildcards
        check_permission({"sinas.functions.*.execute:own": True}, "sinas.functions.analytics.run_report.execute:own") -> True
        check_permission({"custom.*.get:own": True}, "custom.content.get:own") -> True
        check_permission({"custom.*.get:own": True}, "custom.content.post:own") -> False (action mismatch)

        # Scope hierarchy - :all grants :group and :own
        check_permission({"sinas.chats.get:all": True}, "sinas.chats.get:group") -> True
        check_permission({"sinas.chats.get:all": True}, "sinas.chats.get:own") -> True

        # Combined wildcard + scope hierarchy
        check_permission({"custom.*:all": True}, "custom.analytics.query:own") -> True
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


def validate_permission_subset(
    subset_perms: Dict[str, bool],
    superset_perms: Dict[str, bool]
) -> tuple[bool, List[str]]:
    """
    Validate that subset permissions are contained within superset permissions.

    Used for API key creation - ensures API keys can't have more permissions
    than the user's group permissions.

    Uses pattern matching instead of expansion, so works with wildcards and custom permissions.

    Args:
        subset_perms: Permissions to validate (requested API key permissions)
        superset_perms: Permissions that must contain the subset (user's group permissions)

    Returns:
        Tuple of (is_valid, list_of_violations)

    Examples:
        # Valid - user has required permissions via wildcard
        validate_permission_subset(
            {"sinas.users.get:own": True},
            {"sinas.*:all": True}
        ) -> (True, [])

        # Invalid - user doesn't have :all scope
        validate_permission_subset(
            {"sinas.users.post:all": True},
            {"sinas.users.post:own": True}
        ) -> (False, ["sinas.users.post:all"])

        # Valid - multiple permissions covered by user's wildcard
        validate_permission_subset(
            {"titan.content.get:own": True, "titan.analytics.get:own": True},
            {"titan.*.get:own": True}
        ) -> (True, [])
    """
    violations = []

    for perm, value in subset_perms.items():
        if value:  # Only check permissions that are granted (True)
            # Check if user has this permission using pattern matching
            if not check_permission(superset_perms, perm):
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
        "sinas.users.post:all": False,

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

        # Templates (namespace-based)
        "sinas.templates.*.*.post:own": True,
        "sinas.templates.*.*.get:own": True,
        "sinas.templates.*.*.put:own": True,
        "sinas.templates.*.*.delete:own": True,
        "sinas.templates.*.*.render:own": True,
        "sinas.templates.*.*.send:own": True,
        "sinas.templates.default.*.render:group": True,  # Can render default templates
        "sinas.templates.default.*.send:group": True,    # Can send emails with default templates

        # Request Logs
        "sinas.logs.get:own": True,
    },
    "Admins": {
        "sinas.*:all": True,  # Full access to everything
    }
}
