"""Component builder service - compiles TSX via the esbuild builder container."""
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class ComponentBuilderService:
    """Service for compiling component source code via the builder container."""

    def __init__(self, builder_url: str = None):
        self.builder_url = builder_url or settings.builder_url

    async def compile(self, source_code: str) -> dict[str, Any]:
        """
        Compile TSX source code into an IIFE bundle.

        Returns:
            dict with keys:
                - success: bool
                - bundle: str (if success)
                - sourceMap: str (if success)
                - errors: list[dict] (if not success)
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.builder_url}/compile",
                    json={"sourceCode": source_code},
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException:
            logger.error("Builder service timeout")
            return {
                "success": False,
                "errors": [{"text": "Compilation timed out", "location": None}],
            }
        except httpx.ConnectError:
            logger.error("Cannot connect to builder service at %s", self.builder_url)
            return {
                "success": False,
                "errors": [
                    {
                        "text": "Builder service unavailable. Ensure sinas-builder container is running.",
                        "location": None,
                    }
                ],
            }
        except Exception as e:
            logger.error("Builder service error: %s", str(e))
            return {
                "success": False,
                "errors": [{"text": f"Builder error: {str(e)}", "location": None}],
            }
