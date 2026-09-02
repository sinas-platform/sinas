"""The ask_user system tool — let an agent ask the user a question mid-turn.

Opt-in via `system_tools: ["askUser"]`. The tool never executes inline:
the tool round suspends with a `human_input` pending completion (see
`deferred_completions`), the question surfaces as an `input_required`
stream event and via GET /chats/{id}/pending-input, and the answer posted
to POST /chats/{id}/pending-input/{tool_call_id} becomes the tool result
the round resumes with.
"""
from typing import Any

ASK_USER_TOOL_NAME = "ask_user"


def get_ask_user_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": ASK_USER_TOOL_NAME,
            "description": (
                "Ask the user a question and wait for their answer. Use this "
                "when you are blocked on a decision only the user can make — "
                "a missing parameter, a choice between approaches, an "
                "ambiguous request. The conversation pauses until the user "
                "answers (or a timeout passes); the answer comes back as this "
                "tool's result. Ask one clear question per call; offer "
                "options when the realistic answers are enumerable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to put to the user, self-contained and specific.",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional suggested answers to present as choices. "
                            "The user can always answer with free text instead."
                        ),
                    },
                },
                "required": ["question"],
            },
            # deferred_completer marks calls the tool round suspends on
            # instead of executing — see message_service._handle_tool_calls.
            "_metadata": {"system_tool": True, "deferred_completer": "human_input"},
        },
    }
