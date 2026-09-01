from __future__ import annotations

import pytest
import requests
from conftest import StubResponse, StubSession

from lefiya_schedule_bot.line import (
    LineAPIError,
    LineBroadcaster,
    MessageTooLongError,
    utf16_code_units,
)


def test_broadcast_sends_expected_payload_and_headers() -> None:
    session = StubSession(
        [StubResponse(200, headers={"X-Line-Request-Id": "request-1"})]
    )
    broadcaster = LineBroadcaster("secret-token", session=session)

    result = broadcaster.broadcast("今日班表", "retry-key")

    assert result.already_sent is False
    assert result.request_id == "request-1"
    call = session.calls[0]
    assert call["url"] == LineBroadcaster.BROADCAST_URL
    assert call["headers"]["Authorization"] == "Bearer secret-token"
    assert call["headers"]["X-Line-Retry-Key"] == "retry-key"
    assert call["json"] == {"messages": [{"type": "text", "text": "今日班表"}]}
    assert call["timeout"] == (5.0, 20.0)


def test_broadcast_treats_409_as_already_sent() -> None:
    session = StubSession(
        [StubResponse(409, headers={"X-Line-Accepted-Request-Id": "accepted-1"})]
    )

    result = LineBroadcaster("token", session=session).broadcast("text", "retry")

    assert result.already_sent is True
    assert result.request_id == "accepted-1"


def test_broadcast_retries_retryable_statuses_with_identical_payload() -> None:
    sleeps: list[float] = []
    session = StubSession([StubResponse(429), StubResponse(503), StubResponse(204)])

    result = LineBroadcaster("token", session=session, sleeper=sleeps.append).broadcast(
        "text", "same-key"
    )

    assert result.already_sent is False
    assert sleeps == [1, 2]
    assert [call["json"] for call in session.calls] == [session.calls[0]["json"]] * 3
    assert all(
        call["headers"]["X-Line-Retry-Key"] == "same-key" for call in session.calls
    )


def test_broadcast_retries_timeout() -> None:
    sleeps: list[float] = []
    session = StubSession([requests.Timeout("timeout"), StubResponse(200)])

    LineBroadcaster("token", session=session, sleeper=sleeps.append).broadcast(
        "text", "same-key"
    )

    assert sleeps == [1]
    assert len(session.calls) == 2


def test_broadcast_does_not_retry_non_retryable_4xx() -> None:
    session = StubSession([StubResponse(401, {"message": "unauthorized"})])

    with pytest.raises(LineAPIError, match="HTTP 401: unauthorized"):
        LineBroadcaster("token", session=session).broadcast("text", "retry")
    assert len(session.calls) == 1


def test_broadcast_rejects_message_over_utf16_limit() -> None:
    session = StubSession([])
    broadcaster = LineBroadcaster("token", session=session)

    assert utf16_code_units("😀") == 2
    with pytest.raises(MessageTooLongError, match="5001"):
        broadcaster.broadcast("a" * 5_001, "retry")
    assert session.calls == []
