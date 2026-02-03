"""Permission management utilities."""


def matches_permission_pattern(pattern: str, concrete: str) -> bool:
    """
    Check if a concrete permission matches a wildcard pattern.

    Uses pattern matching with URL-style paths for namespaced resources.

    Permission Format:
        <service>.<resource_type>[/<namespace>[/<name>]].<action>:<scope>

    Where:
        - Dots (.) separate service, resource_type, and action
        - Slashes (/) separate namespace and name (optional hierarchical path)
        - Colon (:) separates scope

    Scope Hierarchy:
        - :all grants :own
        - Pattern with :all matches requests for :own

    Args:
        pattern: Permission pattern with potential wildcards
        concrete: Concrete permission to check

    Returns:
        True if concrete permission matches the pattern

    Examples:
        # Service-level wildcard
        matches_permission_pattern("sinas.*:all", "sinas.chats.read:own") -> True
        matches_permission_pattern("sinas.*:all", "sinas.functions/marketing/send.execute:own") -> True

        # Resource type matching
        matches_permission_pattern("sinas.chats.read:own", "sinas.chats.read:own") -> True
        matches_permission_pattern("sinas.chats.*:own", "sinas.chats.read:own") -> True

        # Path-based matching (namespaced resources)
        matches_permission_pattern("sinas.functions/*/*.execute:own", "sinas.functions/marketing/send_email.execute:own") -> True
        matches_permission_pattern("sinas.functions/marketing/*.execute:own", "sinas.functions/marketing/send_email.execute:own") -> True
        matches_permission_pattern("sinas.functions/marketing/send_email.execute:own", "sinas.functions/marketing/send_email.execute:own") -> True

        # Scope hierarchy: :all grants :own
        matches_permission_pattern("sinas.chats.read:all", "sinas.chats.read:own") -> True
    """
    # Split by scope separator
    try:
        pattern_parts, pattern_scope = pattern.rsplit(":", 1)
        concrete_parts, concrete_scope = concrete.rsplit(":", 1)
    except ValueError:
        return False

    # Check scope with hierarchy: :all grants :own
    scope_hierarchy = {"all": ["all", "own"], "own": ["own"], "*": ["all", "own"]}

    allowed_scopes = scope_hierarchy.get(pattern_scope, [pattern_scope])
    if concrete_scope not in allowed_scopes:
        return False

    # Parse pattern: <service>.<resource_type>[/<path>].<action>
    # Split on last '.' to separate action
    try:
        pattern_resource, pattern_action = pattern_parts.rsplit(".", 1)
        concrete_resource, concrete_action = concrete_parts.rsplit(".", 1)
    except ValueError:
        # Invalid format (no action)
        return False

    # Check action match (with wildcard support)
    if pattern_action != "*" and pattern_action != concrete_action:
        return False

    # Parse resource identifier: <service>.<resource_type>[/<path>]
    # Split on first '/' to separate base from path
    if "/" in pattern_resource:
        pattern_base, pattern_path = pattern_resource.split("/", 1)
    else:
        pattern_base = pattern_resource
        pattern_path = None

    if "/" in concrete_resource:
        concrete_base, concrete_path = concrete_resource.split("/", 1)
    else:
        concrete_base = concrete_resource
        concrete_path = None

    # Check base match (<service>.<resource_type>)
    pattern_base_segments = pattern_base.split(".")
    concrete_base_segments = concrete_base.split(".")

    # Handle trailing wildcard in base (e.g., "sinas.*")
    # OR when action is wildcard (e.g., "sinas" with action="*" should match "sinas.mcp_servers")
    if pattern_base_segments[-1] == "*" or (
        pattern_action == "*" and len(pattern_base_segments) < len(concrete_base_segments)
    ):
        # Check prefix matches
        if pattern_base_segments[-1] == "*":
            prefix_segments = pattern_base_segments[:-1]
        else:
            prefix_segments = pattern_base_segments

        if len(concrete_base_segments) < len(prefix_segments):
            return False
        for i, pattern_seg in enumerate(prefix_segments):
            if pattern_seg != "*" and pattern_seg != concrete_base_segments[i]:
                return False
    else:
        # Exact base match required
        if len(pattern_base_segments) != len(concrete_base_segments):
            return False
        for pattern_seg, concrete_seg in zip(
            pattern_base_segments, concrete_base_segments, strict=False
        ):
            if pattern_seg != "*" and pattern_seg != concrete_seg:
                return False

    # Check path match (namespace/name hierarchy)
    if pattern_path is None and concrete_path is None:
        # Both have no path - match
        return True

    if pattern_path is None and concrete_path is not None:
        # Pattern has no path but concrete does
        # If pattern has wildcards (e.g., "sinas.*:all"), it should match all paths
        has_wildcards = pattern_action == "*" or (
            pattern_base_segments and pattern_base_segments[-1] == "*"
        )
        if has_wildcards:
            # Wildcard patterns match resources with or without paths
            return True
        else:
            # Non-wildcard pattern without path doesn't match concrete with path
            return False

    if pattern_path is not None and concrete_path is None:
        # Pattern expects path but concrete has none - no match
        return False

    # Both have paths - match path segments
    pattern_path_segments = pattern_path.split("/")
    concrete_path_segments = concrete_path.split("/")

    # Handle path wildcards
    if len(pattern_path_segments) != len(concrete_path_segments):
        return False

    for pattern_seg, concrete_seg in zip(
        pattern_path_segments, concrete_path_segments, strict=False
    ):
        if pattern_seg != "*" and pattern_seg != concrete_seg:
            return False

    return True


def check_permission(permissions: dict[str, bool], required_permission: str) -> bool:
    """
    Check if user has a permission, supporting wildcard matching and scope hierarchy.

    Checks if user has the required permission either directly, via wildcard patterns,
    or via scope hierarchy (e.g., :all grants :own).

    Works for ANY resource type: sinas.*, custom namespaces (custom.*, acme.*), etc.

    Args:
        permissions: User's permission dictionary (may contain wildcards)
        required_permission: The concrete permission needed

    Returns:
        True if user has permission (directly, via wildcard, or via scope hierarchy), False otherwise

    Examples:
        # Exact match
        check_permission({"sinas.chats.read:own": True}, "sinas.chats.read:own") -> True

        # Wildcard matching - admin has full access
        check_permission({"sinas.*:all": True}, "sinas.chats.read:own") -> True
        check_permission({"sinas.*:all": True}, "sinas.functions/marketing/send.execute:own") -> True

        # Action wildcards
        check_permission({"sinas.chats.*:own": True}, "sinas.chats.read:own") -> True

        # Path-based wildcards (namespaced resources)
        check_permission({"sinas.functions/*/*.execute:own": True}, "sinas.functions/marketing/send_email.execute:own") -> True
        check_permission({"sinas.functions/marketing/*.execute:own": True}, "sinas.functions/marketing/send_email.execute:own") -> True

        # Scope hierarchy - :all grants :own
        check_permission({"sinas.chats.read:all": True}, "sinas.chats.read:own") -> True

        # Custom apps
        check_permission({"titan.*:all": True}, "titan.student_profile.read:own") -> True
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
    subset_perms: dict[str, bool], superset_perms: dict[str, bool]
) -> tuple[bool, list[str]]:
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


# Default role permissions
DEFAULT_ROLE_PERMISSIONS = {
    "GuestUsers": {
        "sinas.*:own": False,  # No access by default
        "sinas.users.read:own": True,
        "sinas.users.update:own": True,
    },
    "Users": {
        # Agents (namespaced: namespace/name)
        # Note: Chats are always linked to agents, permissions checked via agents
        "sinas.agents.create:own": True,
        "sinas.agents.read:own": True,
        "sinas.agents.update:own": True,
        "sinas.agents.delete:own": True,
        "sinas.agents/*/*.chat:own": True,  # Chat with specific agents
        # Functions (namespaced: namespace/name)
        "sinas.functions.create:own": True,
        "sinas.functions.read:own": True,
        "sinas.functions.update:own": True,
        "sinas.functions.delete:own": True,
        "sinas.functions/*/*.execute:own": True,  # Execute specific functions
        # Skills (namespaced: namespace/name)
        "sinas.skills.create:own": True,
        "sinas.skills.read:own": True,
        "sinas.skills.update:own": True,
        "sinas.skills.delete:own": True,
        # Webhooks (non-namespaced)
        "sinas.webhooks.create:own": True,
        "sinas.webhooks.read:own": True,
        "sinas.webhooks.update:own": True,
        "sinas.webhooks.delete:own": True,
        # Schedules (non-namespaced)
        "sinas.schedules.create:own": True,
        "sinas.schedules.read:own": True,
        "sinas.schedules.update:own": True,
        "sinas.schedules.delete:own": True,
        # Executions (runtime - non-namespaced)
        "sinas.executions.read:own": True,
        # Messages (observability - non-namespaced)
        "sinas.messages.read:own": True,
        # Users (non-namespaced)
        "sinas.users.read:own": True,
        "sinas.users.update:own": True,
        # API Keys (non-namespaced - no permission checks in code)
        "sinas.api_keys.create:own": True,
        "sinas.api_keys.read:own": True,
        "sinas.api_keys.delete:own": True,
        # States (namespace-based permissions)
        # Users can access their own states in any namespace
        "sinas.states/*.read:own": True,
        "sinas.states/*.create:own": True,
        "sinas.states/*.update:own": True,
        "sinas.states/*.delete:own": True,
        # Common namespaces: preferences, memory, etc.
        # Templates (namespaced: namespace/name)
        "sinas.templates.create:own": True,
        "sinas.templates.read:own": True,
        "sinas.templates.update:own": True,
        "sinas.templates.delete:own": True,
        "sinas.templates/*/*.render:own": True,  # Render specific templates
        "sinas.templates/*/*.send:own": True,  # Send with specific templates
        # Request Logs (non-namespaced)
        "sinas.logs.read:own": True,
    },
    "Admins": {
        "sinas.*:all": True,  # Full access to everything including:
        # Role Management (admin-only)
        # "sinas.roles.create:all"
        # "sinas.roles.read:all"
        # "sinas.roles.update:all"
        # "sinas.roles.delete:all"
        # "sinas.roles.manage_members:all"
        # "sinas.roles.manage_permissions:all"
        # MCP Servers (admin-only)
        # "sinas.mcp_servers.create:all"
        # "sinas.mcp_servers.read:all"
        # "sinas.mcp_servers.update:all"
        # "sinas.mcp_servers.delete:all"
        # "sinas.mcp_tools.read:all"
        # "sinas.mcp_tools.execute:all"
        # LLM Providers (admin-only)
        # "sinas.llm_providers.create:all"
        # "sinas.llm_providers.read:all"
        # "sinas.llm_providers.update:all"
        # "sinas.llm_providers.delete:all"
        # Packages (admin-only)
        # "sinas.packages.install:all"
        # "sinas.packages.delete:all"
        # Config (admin-only)
        # "sinas.config.validate:all"
        # "sinas.config.apply:all"
        # "sinas.config.export:all"
        # Containers (admin-only)
        # "sinas.containers.read:all"
        # "sinas.containers.update:all"
        # "sinas.containers.delete:all"
        # Workers (admin-only)
        # "sinas.workers.read:all"
        # "sinas.workers.create:all"
        # "sinas.workers.update:all"
        # "sinas.workers.delete:all"
        # Advanced Executions (admin-only)
        # "sinas.executions.read:all"
        # "sinas.executions.update:all"
        # Advanced States (admin-only, namespace-based)
        # "sinas.states/*.read:all" - Read all states in any namespace
        # "sinas.states/*.create:all" - Create states in any namespace
        # "sinas.states/*.update:all" - Update any states
        # "sinas.states/*.delete:all" - Delete any states
        #
        # Shared namespace examples (for team sharing):
        # "sinas.states/api_keys.read:all" - Read shared API keys
        # "sinas.states/api_keys.create:all" - Create shared API keys
        # "sinas.states/configs.read:all" - Read shared configs
    },
}
