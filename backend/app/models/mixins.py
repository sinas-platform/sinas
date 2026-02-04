"""Permission-aware query mixin for models."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_permission


class PermissionMixin:
    """
    Mixin for models that need permission-aware querying.

    Conventions (auto-detected):
    - permission_base = "sinas.<table_name>"
    - namespace field = "namespace" (if exists)
    - name field = "name" (if exists)
    - ownership field = "user_id" (if exists)

    No database changes - just adds methods.

    Usage:
        class Agent(Base, PermissionMixin):
            __tablename__ = "agents"
            id: Mapped[uuid_pk]
            user_id: Mapped[uuid.UUID]  # Auto-detected as ownership
            namespace: Mapped[str]      # Auto-detected as namespaced
            name: Mapped[str]           # Auto-detected as name field
            ...

        # In endpoint:
        agents = await Agent.list_with_permissions(
            db, user_id, permissions, action="read"
        )
    """

    @classmethod
    def _permission_base(cls) -> str:
        """Auto-detect: sinas.<table_name>"""
        return f"sinas.{cls.__tablename__}"

    @classmethod
    def _is_namespaced(cls) -> bool:
        """Check if model has namespace field"""
        return hasattr(cls, "namespace")

    @classmethod
    def _has_ownership(cls) -> bool:
        """Check if model has user_id field"""
        return hasattr(cls, "user_id")

    @classmethod
    async def list_with_permissions(
        cls,
        db: AsyncSession,
        user_id: str,
        permissions: dict[str, bool],
        action: str,
        additional_filters=None,
        skip: int = 0,
        limit: int = 100,
    ):
        """
        List resources filtered by permissions.

        Args:
            db: Database session
            user_id: Current user ID
            permissions: User's permission dictionary
            action: Action being performed (e.g., "read", "execute")
            additional_filters: Optional SQLAlchemy filter expressions
            skip: Pagination offset
            limit: Pagination limit

        Returns:
            List of resources the user can access
        """
        # Build base query
        query = select(cls)

        # Build permission strings
        perm_base = cls._permission_base()

        if cls._is_namespaced():
            # Namespaced resource: sinas.agents/*/*.read:all
            all_perm = f"{perm_base}/*/*.{action}:all"
            own_perm = f"{perm_base}/*/*.{action}:own"
        else:
            # Non-namespaced: sinas.users.read:all
            all_perm = f"{perm_base}.{action}:all"
            own_perm = f"{perm_base}.{action}:own"

        has_all = check_permission(permissions, all_perm)

        if not has_all:
            # Check :own permission
            has_own = check_permission(permissions, own_perm)

            if has_own:
                # Filter by ownership if user_id exists
                if cls._has_ownership():
                    query = query.where(cls.user_id == user_id)
                # else: has permission but no ownership = access all
            else:
                # Check for namespace-specific permissions (if namespaced)
                if cls._is_namespaced():
                    accessible_namespaces = cls._get_accessible_namespaces(permissions, action)

                    if accessible_namespaces:
                        # User can access specific namespaces OR their own resources
                        filters = [cls.namespace.in_(accessible_namespaces)]

                        if cls._has_ownership():
                            filters.append(cls.user_id == user_id)

                        query = query.where(or_(*filters))
                    else:
                        # No access at all - return empty
                        return []
                else:
                    # Non-namespaced, no :all, no :own = no access
                    return []

        # Add additional filters
        if additional_filters is not None:
            query = query.where(additional_filters)

        # Pagination
        query = query.offset(skip).limit(limit)

        # Execute
        result = await db.execute(query)
        return list(result.scalars().all())

    @classmethod
    async def get_with_permissions(
        cls,
        db: AsyncSession,
        user_id: str,
        permissions: dict[str, bool],
        action: str,
        resource_id=None,
        namespace: str = None,
        name: str = None,
    ):
        """
        Get single resource with permission check.

        Args:
            db: Database session
            user_id: Current user ID
            permissions: User's permission dictionary
            action: Action being performed (e.g., "read", "update", "delete")
            resource_id: For non-namespaced resources (e.g., user ID)
            namespace: For namespaced resources (e.g., agent namespace)
            name: For namespaced resources (e.g., agent name)

        Returns:
            Resource instance

        Raises:
            HTTPException(404): Resource not found
            HTTPException(403): Permission denied
        """
        # Load resource
        query = select(cls)

        if namespace is not None and name is not None:
            # Namespaced lookup
            if not cls._is_namespaced():
                raise ValueError(f"{cls.__name__} is not namespaced")
            query = query.where(cls.namespace == namespace, cls.name == name)
        elif resource_id is not None:
            # ID lookup
            query = query.where(cls.id == resource_id)
        else:
            raise ValueError("Must provide either resource_id or namespace/name")

        result = await db.execute(query)
        resource = result.scalar_one_or_none()

        if not resource:
            raise HTTPException(404, f"{cls.__name__} not found")

        # Check permission
        if not resource.can_user_access(user_id, permissions, action):
            raise HTTPException(403, f"Not authorized to {action} this resource")

        return resource

    def can_user_access(
        self,
        user_id: str,
        permissions: dict[str, bool],
        action: str,
    ) -> bool:
        """
        Check if user can access this specific resource instance.

        Args:
            user_id: Current user ID
            permissions: User's permission dictionary
            action: Action being performed (e.g., "read", "update", "delete")

        Returns:
            True if user has permission, False otherwise
        """
        # Build permission string for this resource
        perm_base = self._permission_base()

        if self._is_namespaced():
            # Namespaced resource: sinas.agents/marketing/chatbot.read
            perm_base = f"{perm_base}/{self.namespace}/{self.name}.{action}"
        else:
            # Non-namespaced: sinas.users.read
            perm_base = f"{perm_base}.{action}"

        # Check :all
        if check_permission(permissions, f"{perm_base}:all"):
            return True

        # Check :own
        if check_permission(permissions, f"{perm_base}:own"):
            # Check ownership if user_id exists
            if self._has_ownership():
                return str(self.user_id) == user_id
            else:
                # No ownership field = anyone with :own can access
                return True

        return False

    @classmethod
    def _get_accessible_namespaces(
        cls,
        permissions: dict[str, bool],
        action: str,
    ) -> set[str]:
        """
        Extract accessible namespaces from user permissions.

        Args:
            permissions: User's permission dictionary
            action: Action being performed (e.g., "read", "execute", "update")

        Returns:
            Set of namespace strings user can access for this specific action
        """
        accessible = set()

        # Look for permissions matching this resource type
        # e.g., "sinas.functions/marketing/*.execute:all"
        prefix = f"{cls._permission_base()}/"

        for perm_key, has_perm in permissions.items():
            if not has_perm:
                continue

            if perm_key.startswith(prefix):
                # Extract action from permission to ensure it matches
                # "sinas.functions/marketing/*.execute:all" -> action is "execute"
                try:
                    # Remove scope suffix (:all/:own)
                    perm_without_scope = perm_key.split(":")[0]
                    # Extract action (last part after final dot)
                    perm_action = perm_without_scope.split(".")[-1]

                    # Only include if action matches OR is wildcard
                    if perm_action != action and perm_action != "*":
                        continue

                    # Extract namespace
                    after_prefix = perm_key[len(prefix) :]
                    namespace_part = after_prefix.split("/")[0]

                    if namespace_part != "*":
                        accessible.add(namespace_part)
                except:
                    continue

        return accessible
