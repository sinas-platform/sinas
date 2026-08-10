"""Unit tests for cached-token extraction in the OpenAI provider.

OpenAI's automatic prompt caching reports the cached portion of the prompt
in usage.prompt_tokens_details.cached_tokens; Azure and OpenAI-compatible
endpoints (e.g. Gemini's compat layer) reuse this code path.
"""
from types import SimpleNamespace

from app.providers import OpenAIProvider


def _provider():
    return OpenAIProvider(api_key="test-key")


def test_extract_usage_with_cached_tokens():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=5000,
            completion_tokens=100,
            total_tokens=5100,
            prompt_tokens_details=SimpleNamespace(cached_tokens=4096),
        )
    )
    usage = _provider().extract_usage(response)
    # OpenAI's prompt_tokens already includes the cached portion — no summing
    assert usage["prompt_tokens"] == 5000
    assert usage["cache_read_tokens"] == 4096
    assert usage["cache_write_tokens"] == 0  # OpenAI has no write premium
    assert usage["total_tokens"] == 5100


def test_extract_usage_without_details():
    """Endpoints that don't report prompt_tokens_details (older APIs, some
    compat layers) must not break — cache fields default to 0."""
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=200, completion_tokens=50, total_tokens=250,
            prompt_tokens_details=None,
        )
    )
    usage = _provider().extract_usage(response)
    assert usage["prompt_tokens"] == 200
    assert usage["cache_read_tokens"] == 0
    assert usage["cache_write_tokens"] == 0


def test_extract_usage_details_without_cached_tokens():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=200, completion_tokens=50, total_tokens=250,
            prompt_tokens_details=SimpleNamespace(cached_tokens=None),
        )
    )
    usage = _provider().extract_usage(response)
    assert usage["cache_read_tokens"] == 0


def test_extract_usage_no_usage():
    usage = _provider().extract_usage(SimpleNamespace(usage=None))
    assert usage == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
