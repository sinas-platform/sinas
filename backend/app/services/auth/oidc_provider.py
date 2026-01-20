"""OIDC/OAuth2 authentication provider."""
import httpx
from jose import jwt, JWTError
from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)


class OIDCAuthProvider:
    """OIDC authentication provider configured from environment."""

    def __init__(self):
        from app.core.config import settings
        self.issuer = settings.oidc_issuer
        self.audience = settings.oidc_audience
        self.groups_claim = settings.oidc_groups_claim or "groups"
        self.jwks_uri = f"{self.issuer}/.well-known/jwks.json"
        self._jwks = None

    async def validate_token(self, token: str) -> Optional[Dict]:
        """
        Validate OIDC token and extract user information.

        Args:
            token: JWT token from external IdP

        Returns:
            Dict with user info and groups, or None if invalid
            {
                "external_user_id": "...",
                "email": "...",
                "name": "...",
                "external_groups": ["group1", "group2"],
                "metadata": {...}
            }
        """
        try:
            # Fetch JWKS if not cached
            if not self._jwks:
                async with httpx.AsyncClient() as client:
                    response = await client.get(self.jwks_uri, timeout=10.0)
                    response.raise_for_status()
                    self._jwks = response.json()

            # Validate JWT
            payload = jwt.decode(
                token,
                self._jwks,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer
            )

            # Extract groups (can be simple list or object list)
            raw_groups = payload.get(self.groups_claim, [])
            external_groups = []
            for g in raw_groups:
                if isinstance(g, str):
                    external_groups.append(g)
                elif isinstance(g, dict):
                    # Support both {"id": "..."} and {"name": "..."}
                    external_groups.append(g.get("id", g.get("name")))

            return {
                "external_user_id": payload.get("sub"),
                "email": payload.get("email"),
                "name": payload.get("name"),
                "external_groups": external_groups,
                "metadata": payload
            }

        except JWTError as e:
            logger.warning(f"JWT validation failed: {e}")
            return None
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch JWKS: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error validating OIDC token: {e}")
            return None
