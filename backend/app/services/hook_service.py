"""Message lifecycle hooks — run functions before/after agent messages."""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.queue_service import queue_service

logger = logging.getLogger(__name__)


@dataclass
class HookResult:
    """Result of running hooks."""
    blocked: bool = False
    reply: Optional[str] = None
    mutated_content: Optional[str] = None


async def run_hooks(
    hooks: list[dict[str, Any]],
    message_content: str,
    message_role: str,
    chat_id: str,
    agent_namespace: str,
    agent_name: str,
    user_id: str,
    session_key: Optional[str] = None,
) -> HookResult:
    """Run a list of hooks in order.

    Sync hooks execute sequentially; async hooks fire-and-forget.
    A sync hook can mutate the message content or block the pipeline.
    """
    result = HookResult()
    current_content = message_content

    for hook_config in hooks:
        func_ref = hook_config.get("function", "")
        is_async = hook_config.get("async", False)
        on_timeout = hook_config.get("on_timeout", "passthrough")

        parts = func_ref.split("/", 1)
        if len(parts) != 2:
            logger.warning(f"Invalid hook function reference: {func_ref}")
            continue

        func_namespace, func_name = parts

        # Build hook payload
        hook_input = {
            "message": {"role": message_role, "content": current_content},
            "chat_id": chat_id,
            "agent": {"namespace": agent_namespace, "name": agent_name},
            "session_key": session_key,
            "user_id": user_id,
        }

        execution_id = str(uuid.uuid4())

        if is_async:
            # Fire and forget
            try:
                await queue_service.enqueue_function(
                    function_namespace=func_namespace,
                    function_name=func_name,
                    input_data=hook_input,
                    execution_id=execution_id,
                    trigger_type="HOOK",
                    trigger_id=f"hook:{agent_namespace}/{agent_name}",
                    user_id=user_id,
                    chat_id=chat_id,
                )
            except Exception as e:
                logger.warning(f"Failed to enqueue async hook {func_ref}: {e}")
            continue

        # Sync hook — wait for result
        try:
            hook_result = await queue_service.enqueue_and_wait(
                function_namespace=func_namespace,
                function_name=func_name,
                input_data=hook_input,
                execution_id=execution_id,
                trigger_type="HOOK",
                trigger_id=f"hook:{agent_namespace}/{agent_name}",
                user_id=user_id,
                chat_id=chat_id,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Hook {func_ref} timed out, on_timeout={on_timeout}")
            if on_timeout == "block":
                result.blocked = True
                result.reply = f"Message blocked: hook '{func_ref}' timed out"
                return result
            continue
        except Exception as e:
            logger.error(f"Hook {func_ref} failed: {e}", exc_info=True)
            if on_timeout == "block":
                result.blocked = True
                result.reply = f"Message blocked: hook '{func_ref}' failed: {e}"
                return result
            continue

        if not hook_result or not isinstance(hook_result, dict):
            continue

        # Check for block
        if hook_result.get("block"):
            result.blocked = True
            result.reply = hook_result.get("reply", "Message blocked by hook")
            return result

        # Check for content mutation
        if "content" in hook_result:
            current_content = hook_result["content"]
            result.mutated_content = current_content

    return result
