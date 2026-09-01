from __future__ import annotations

from datetime import date

import pytest
import requests
from conftest import (
    StubResponse,
    StubSession,
    category,
    menu_hours_response,
    menu_response,
)

from lefiya_schedule_bot.ichef import IChefAPIError, IChefClient
from lefiya_schedule_bot.models import MenuDataError


def test_fetch_schedules_calls_both_graphql_operations() -> None:
    session = StubSession(
        [
            StubResponse(200, menu_hours_response("uuid-1")),
            StubResponse(200, menu_response([category("20260901 午安")])),
        ]
    )
    client = IChefClient("public-id", session=session)

    schedules = client.fetch_schedules()

    assert schedules[date(2026, 9, 1)].fairies[0].name == "芙蘭"
    assert session.calls[0]["json"]["variables"]["publicId"] == "public-id"
    assert session.calls[1]["json"]["variables"]["categoriesSnapshotUuids"] == [
        "uuid-1"
    ]
    assert session.calls[0]["timeout"] == (5.0, 20.0)


def test_fetch_schedules_returns_empty_when_no_category_uuid() -> None:
    session = StubSession([StubResponse(200, menu_hours_response())])

    assert IChefClient("public-id", session=session).fetch_schedules() == {}
    assert len(session.calls) == 1


def test_ichef_retries_timeout_429_and_5xx() -> None:
    sleeps: list[float] = []
    session = StubSession(
        [
            requests.Timeout("timeout"),
            StubResponse(429),
            StubResponse(200, menu_hours_response()),
        ]
    )
    client = IChefClient("public-id", session=session, sleeper=sleeps.append)

    assert client.fetch_schedules() == {}
    assert sleeps == [1, 2]


def test_ichef_raises_after_retry_exhaustion() -> None:
    session = StubSession([StubResponse(500), StubResponse(502), StubResponse(503)])

    with pytest.raises(IChefAPIError, match="failed after retries"):
        IChefClient(
            "public-id", session=session, sleeper=lambda _: None
        ).fetch_schedules()


@pytest.mark.parametrize(
    "response",
    [
        StubResponse(400, {"message": "bad request"}),
        StubResponse(200, {"errors": [{"message": "GraphQL failed"}]}),
        StubResponse(200, json_error=requests.JSONDecodeError("bad", "x", 0)),
    ],
)
def test_ichef_rejects_non_retryable_or_invalid_responses(
    response: StubResponse,
) -> None:
    with pytest.raises(MenuDataError):
        IChefClient("public-id", session=StubSession([response])).fetch_schedules()
