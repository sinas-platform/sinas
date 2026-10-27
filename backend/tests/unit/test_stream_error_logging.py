"""An error that ends a started SSE stream is logged as itself.

Once a streaming response has started, Starlette reports an exception that
has a handler (Redis' ResponseError has one, for the 503 on a full Redis)
only as "Caught handled exception, but response already started". The
stream generators log the original error first.
"""

import pytest
from redis.exceptions import OutOfMemoryError

from app.api.runtime.endpoints.chats import with_heartbeat
from app.core.streaming import logged_stream

_OOM = OutOfMemoryError(
    "Command # 1 (XADD sinas:stream:c * data {\"secret\": 1}) of pipeline caused error: "
    "(\"command not allowed when used memory > 'maxmemory'.\",)"
)


async def _events(fail: BaseException):
    yield {"event": "message", "data": "1"}
    raise fail


@pytest.mark.parametrize("wrap", [
    lambda g: logged_stream(g, "test stream"),
    lambda g: with_heartbeat(g),
], ids=["logged_stream", "with_heartbeat"])
async def test_a_full_redis_is_logged_by_name(wrap, caplog):
    seen = []
    with caplog.at_level("ERROR"), pytest.raises(OutOfMemoryError):
        async for item in wrap(_events(_OOM)):
            seen.append(item)
    assert seen == [{"event": "message", "data": "1"}]
    (record,) = [r for r in caplog.records if "mid-response" in r.getMessage()]
    assert "Redis is full: XADD refused" in record.getMessage()
    assert "secret" not in record.getMessage()


async def test_other_errors_are_logged_with_their_traceback(caplog):
    with caplog.at_level("ERROR"), pytest.raises(ValueError):
        async for _ in logged_stream(_events(ValueError("bad")), "test stream"):
            pass
    (record,) = [r for r in caplog.records if "mid-response" in r.getMessage()]
    assert "ValueError: bad" in record.getMessage() and record.exc_info
