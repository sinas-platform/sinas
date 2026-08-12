"""Round-tripping provider-specific extra fields on tool calls.

Gemini 3 (via its OpenAI-compat endpoint) rejects tool-loop turns with
"400: Function call is missing a thought_signature in functionCall parts"
unless the signature it emitted on each function call is sent back verbatim
in the conversation history. Sinas used to reconstruct tool calls "clean" at
two points; these tests pin that extras now survive every hop:

  provider response -> format_tool_calls -> persisted Message.tool_calls
  -> history rebuild -> request params
"""

from types import SimpleNamespace

from app.providers import OpenAIProvider


def _sdk_tool_call(**extra):
    """Mimic an OpenAI-SDK tool call object; pydantic puts unknown response
    fields in model_extra."""
    fn = SimpleNamespace(name="search", arguments='{"q": "x"}', model_extra=None)
    tc = SimpleNamespace(
        id="call_1", type="function", function=fn, model_extra=extra or None
    )
    return tc


class TestFormatToolCalls:
    def test_preserves_call_level_extras(self):
        tc = _sdk_tool_call(thought_signature="sig-abc")
        (formatted,) = OpenAIProvider(api_key="k").format_tool_calls([tc])
        assert formatted["thought_signature"] == "sig-abc"
        # standard fields intact
        assert formatted["id"] == "call_1"
        assert formatted["function"]["name"] == "search"

    def test_preserves_nested_extra_content(self):
        tc = _sdk_tool_call(
            extra_content={"google": {"thought_signature": "sig-abc"}}
        )
        (formatted,) = OpenAIProvider(api_key="k").format_tool_calls([tc])
        assert formatted["extra_content"] == {
            "google": {"thought_signature": "sig-abc"}
        }

    def test_preserves_function_level_extras(self):
        tc = _sdk_tool_call()
        tc.function.model_extra = {"thought_signature": "sig-fn"}
        (formatted,) = OpenAIProvider(api_key="k").format_tool_calls([tc])
        assert formatted["function"]["thought_signature"] == "sig-fn"
        assert formatted["function"]["arguments"] == '{"q": "x"}'

    def test_no_extras_unchanged(self):
        (formatted,) = OpenAIProvider(api_key="k").format_tool_calls(
            [_sdk_tool_call()]
        )
        assert set(formatted) == {"id", "type", "function"}


class TestDownstreamPassthrough:
    """The hops after the provider must not re-strip what we preserved."""

    def test_validate_tool_calls_keeps_extras(self):
        from app.services.tool_execution import validate_tool_calls

        tc = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": "{}"},
            "thought_signature": "sig-abc",
        }
        (validated,) = validate_tool_calls([tc])
        assert validated["thought_signature"] == "sig-abc"

    async def test_history_rebuild_keeps_extras(self, db, test_user):
        from app.models import Chat, Message
        from app.services.conversation_history import build_conversation_history
        from app.services.skill_tools import SkillToolConverter

        chat = Chat(user_id=test_user.id, title="t")
        db.add(chat)
        await db.flush()
        db.add(Message(chat_id=chat.id, role="user", content="find x"))
        db.add(
            Message(
                chat_id=chat.id,
                role="assistant",
                content=None,
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                    "thought_signature": "sig-abc",
                    "description": "ui-only, must be stripped",
                }],
            )
        )
        db.add(
            Message(
                chat_id=chat.id, role="tool", tool_call_id="call_1",
                name="search", content='{"found": true}',
            )
        )
        await db.flush()

        messages = await build_conversation_history(
            db=db, chat=chat, skill_converter=SkillToolConverter(),
            inject_context=False, user_id=str(test_user.id),
        )
        assistant = next(m for m in messages if m.get("tool_calls"))
        (tc,) = assistant["tool_calls"]
        assert tc["thought_signature"] == "sig-abc"
        assert "description" not in tc  # UI-only strip still applies
