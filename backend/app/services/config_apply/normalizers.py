"""
Normalization helpers for config references
"""

from app.schemas.config import OwnershipSkip


def should_skip_existing(
    existing,
    managed_by: str,
    config_name: str,
    config_hash: str,
    resource_type: str,
    resource_name: str,
    track_change,
    warnings: list[str],
) -> bool:
    """Check if an existing resource should be skipped during config apply.

    Handles:
    - Inactive (soft-deleted) resources: reclaim ownership and proceed with update
    - Mismatched managed_by: warn and skip (resource owned by another config/package)
    - Unchanged checksum: skip (already up to date)

    Returns True if the resource should be skipped (no update needed).
    Returns False if the resource should be updated.
    """
    # Inactive resources can be reclaimed by any manager
    if hasattr(existing, "is_active") and not existing.is_active:
        existing.managed_by = managed_by
        existing.config_name = config_name
        # Fall through to checksum check — will be updated below
    elif existing.managed_by and existing.managed_by != managed_by:
        warnings.append(
            OwnershipSkip(f"{resource_type.title()} '{resource_name}' exists but is managed by '{existing.managed_by}'. Skipping.")
        )
        track_change("unchanged", resource_type, resource_name)
        return True

    # Check if content has changed
    is_active = getattr(existing, "is_active", True)
    if existing.config_checksum == config_hash and is_active:
        track_change("unchanged", resource_type, resource_name)
        return True

    return False


