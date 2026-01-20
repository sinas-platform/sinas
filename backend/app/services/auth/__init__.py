"""Authentication provider services."""
from typing import Optional
from .oidc_provider import OIDCAuthProvider

_oidc_provider = None


def get_oidc_provider() -> Optional[OIDCAuthProvider]:
    """Get OIDC provider singleton."""
    global _oidc_provider
    from app.core.config import settings

    if not settings.external_auth_enabled or not settings.oidc_issuer:
        return None

    if not _oidc_provider:
        _oidc_provider = OIDCAuthProvider()

    return _oidc_provider
