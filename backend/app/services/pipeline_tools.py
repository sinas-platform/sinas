"""Pipeline-to-tool converter — exposes asTool pipelines as agent tools.

A tool-invoked pipeline runs inline (the interactive chat path) under the
pipeline's syncTimeoutSeconds budget, executing as the chat's user. Agent steps
inside it are delegation-depth-checked so agent→pipeline→agent chains stay
bounded (issue #90 protections apply unchanged).
"""
import asyncio
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_user_permissions
from app.core.permissions import check_permission
from app.models.execution import TriggerType
from app.models.pipeline import Pipeline

logger = logging.getLogger(__name__)

PIPELINE_TOOL_PREFIX = "pipeline_"


class PipelineToolConverter:
    """Converts asTool pipelines to OpenAI-format tools and executes them."""

    async def get_available_pipelines(
        self,
        db: AsyncSession,
        user_id: str,
        enabled_pipelines: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Build tool definitions for enabled asTool pipelines."""
        if not enabled_pipelines:
            return []

        from app.services.resource_resolver import resolve_resource_refs

        resolved = await resolve_resource_refs(db, enabled_pipelines, Pipeline)

        tools = []
        for ref, pipeline in resolved:
            if not pipeline.is_active:
                continue
            if not pipeline.as_tool:
                logger.warning(f"Pipeline '{ref}' is enabled for an agent but has asTool=false; skipping")
                continue

            description = pipeline.tool_description or pipeline.description or f"Run pipeline {ref}"
            tools.append({
                "type": "function",
                "function": {
                    "name": f"{PIPELINE_TOOL_PREFIX}{pipeline.namespace}__{pipeline.name}",
                    "description": description,
                    "parameters": pipeline.input_schema or {"type": "object", "properties": {}},
                    "_metadata": {
                        "type": "pipeline",
                        "namespace": pipeline.namespace,
                        "name": pipeline.name,
                    },
                },
            })
        return tools

    async def execute_pipeline_tool(
        self,
        db: AsyncSession,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        user_token: str,
        enabled_pipelines: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Execute a pipeline tool call inline (sync mode)."""
        from app.services import pipeline_runner
        from app.services.pipeline_runner import PipelineBusyError

        if not tool_name.startswith(PIPELINE_TOOL_PREFIX):
            return {"error": f"Invalid pipeline tool name: {tool_name}"}
        parts = tool_name[len(PIPELINE_TOOL_PREFIX):].split("__", 1)
        if len(parts) != 2:
            return {"error": f"Invalid pipeline tool name: {tool_name}"}
        namespace, name = parts
        ref = f"{namespace}/{name}"

        # SECURITY: validate against the agent's enabled list (supports wildcards)
        from app.services.resource_resolver import matches_ref_pattern

        if enabled_pipelines is not None and not matches_ref_pattern(ref, enabled_pipelines):
            logger.warning(f"Security: LLM attempted to call non-enabled pipeline '{ref}'")
            return {
                "error": "Pipeline not enabled",
                "message": f"Pipeline '{ref}' is not enabled for this agent.",
            }

        pipeline = await Pipeline.get_by_name(db, namespace, name)
        if not pipeline or not pipeline.is_active or not pipeline.as_tool:
            return {"error": f"Pipeline '{ref}' not found, inactive, or not exposed as a tool"}

        user_permissions = await get_user_permissions(db, user_id)
        if not check_permission(user_permissions, f"sinas.pipelines/{ref}.run:own"):
            return {
                "error": "Permission denied",
                "message": f"You don't have permission to run pipeline '{ref}'.",
            }

        # Bound agent steps by the existing delegation-depth counter.
        from app.services.delegation import child_depth_or_error

        agent_depth, depth_error = child_depth_or_error()
        if depth_error and any(s.get("type") == "agent" for s in (pipeline.steps or [])):
            return {"error": depth_error}

        try:
            outcome = await asyncio.wait_for(
                pipeline_runner.run_pipeline(
                    str(pipeline.id),
                    arguments or {},
                    trigger_type=TriggerType.AGENT.value,
                    trigger_id=f"tool:{user_id}",
                    user_id=str(user_id),
                    user_token=user_token,
                    agent_depth=agent_depth,
                    sync=True,
                ),
                timeout=pipeline.sync_timeout_seconds,
            )
        except PipelineBusyError as e:
            return {
                "error": "Pipeline busy",
                "message": f"A run of '{ref}' is already in progress (run_id={e.active_run_id}). Try again shortly.",
            }
        except asyncio.TimeoutError:
            return {
                "error": "Pipeline timed out",
                "message": (
                    f"Pipeline '{ref}' exceeded its {pipeline.sync_timeout_seconds}s budget. "
                    "Completed steps may have had side effects."
                ),
            }

        if outcome["status"] == "succeeded":
            return {"output": outcome["output"], "run_id": outcome["run_id"]}
        return {
            "error": f"Pipeline run {outcome['status']}",
            "message": outcome.get("error"),
            "run_id": outcome["run_id"],
            "steps": outcome.get("steps", []),
        }
