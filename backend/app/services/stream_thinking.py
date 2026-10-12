"""What a streaming client sees of the model's thinking.

Providers put the model's thinking on stream chunks as `thinking`. The text
can say things the answer doesn't (the system prompt, tool results, the
model's reasons), so a client only gets it when it asks
(`include_thinking`). Every client gets the signal itself: one empty
`thinking` when the model starts thinking, so it can show the model is
working before the first word.
"""
from typing import Any, Optional


class ThinkingFilter:
    """Applied to each chunk on its way out, in order; one per stream."""

    def __init__(self, include_text: bool):
        self.include_text = include_text
        self.announced = False

    def __call__(self, chunk: dict[str, Any]) -> Optional[dict[str, Any]]:
        """The chunk to send, or None to send nothing."""
        if chunk.get("content") or chunk.get("tool_calls") or chunk.get("type"):
            # The answer (or a tool call) has started: a later thinking
            # round, after tool results, is announced again.
            self.announced = False
        if "thinking" not in chunk or self.include_text:
            return chunk
        rest = {k: v for k, v in chunk.items() if k != "thinking"}
        if not self.announced:
            self.announced = True
            return {**rest, "thinking": ""}
        if any(v is not None for v in rest.values()):
            return rest
        return None
