"""Component builder service - compiles TSX via the esbuild builder container."""
import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import settings

logger = logging.getLogger(__name__)


class ComponentBuilderService:
    """Service for compiling component source code via the builder container."""

    def __init__(self, builder_url: str = None):
        self.builder_url = settings.builder_url if builder_url is None else builder_url

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
        if not self.builder_url:
            # The lite profile ships without a builder (BUILDER_URL="").
            return {
                "success": False,
                "errors": [
                    {
                        "text": "No component builder is configured on this instance "
                        "(BUILDER_URL is empty), so components can't be compiled.",
                        "location": None,
                    }
                ],
            }
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


# Compiles running in this process, kept referenced so they aren't collected
# mid-flight (asyncio holds only weak references to tasks).
_running: set[asyncio.Task] = set()


def schedule_compile(component_id) -> None:
    """Compile a component in the background (fire and forget)."""
    task = asyncio.create_task(compile_component(component_id))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def compile_component(component_id) -> None:
    """Compile a component's current source and record the result.

    Holds no DB connection during the build (two short transactions, each
    committed: this runs outside get_db). A result is saved only if the
    source is still the one compiled — a quicker later compile must not be
    overwritten by an older one. The last good bundle stays in place while
    a new build runs or after one fails, so a working component keeps
    rendering; compile_status and compile_errors tell the author.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.component import Component

    source_code = None
    try:
        async with AsyncSessionLocal() as db:
            component = (
                await db.execute(select(Component).where(Component.id == component_id))
            ).scalar_one_or_none()
            if not component:
                return
            source_code = component.source_code
            component.compile_status = "compiling"
            await db.commit()

        compile_result = await ComponentBuilderService().compile(source_code)
    except Exception as e:  # never leave the row at "compiling"
        logger.exception("Compiling component %s failed", component_id)
        compile_result = {
            "success": False,
            "errors": [{"text": f"Compilation failed: {e}", "location": None}],
        }

    try:
        async with AsyncSessionLocal() as db:
            component = (
                await db.execute(
                    select(Component).where(Component.id == component_id).with_for_update()
                )
            ).scalar_one_or_none()
            if not component or source_code is None or component.source_code != source_code:
                return  # deleted, or edited since: its own compile records the result
            if compile_result["success"]:
                component.compiled_bundle = compile_result["bundle"]
                component.source_map = compile_result.get("sourceMap")
                component.compile_status = "success"
                component.compile_errors = None
            else:
                component.compile_status = "error"
                component.compile_errors = compile_result.get("errors", [])
            await db.commit()
    except Exception:
        logger.exception("Recording the compile result of component %s failed", component_id)


async def resume_interrupted_compiles() -> int:
    """At startup: compile again whatever a restart interrupted (left at
    pending or compiling), or it would stay there — and the editor poll it —
    forever. Harmless on several replicas: results are idempotent."""
    from app.core.database import AsyncSessionLocal
    from app.models.component import Component

    async with AsyncSessionLocal() as db:
        ids = (
            await db.execute(
                select(Component.id).where(
                    Component.compile_status.in_(("pending", "compiling")),
                    Component.is_active == True,  # noqa: E712
                )
            )
        ).scalars().all()
    for component_id in ids:
        schedule_compile(component_id)
    return len(ids)
