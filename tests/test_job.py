from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from lefiya_schedule_bot.job import (
    DeadlineExceededError,
    ScheduleJob,
    ScheduleUnavailableError,
    daily_retry_key,
)
from lefiya_schedule_bot.line import BroadcastResult
from lefiya_schedule_bot.models import DailySchedule, Fairy, MenuDataError, Schedule

TAIPEI = ZoneInfo("Asia/Taipei")


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FakeIChef:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = iter(outcomes)
        self.calls = 0

    def fetch_schedules(self) -> dict[date, DailySchedule]:
        self.calls += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeBroadcaster:
    def __init__(self, *, already_sent: bool = False) -> None:
        self.already_sent = already_sent
        self.calls: list[tuple[str, str]] = []

    def broadcast(self, text: str, retry_key: str) -> BroadcastResult:
        self.calls.append((text, retry_key))
        return BroadcastResult(self.already_sent, "line-request")


def make_schedule(service_date: date) -> DailySchedule:
    return DailySchedule(
        service_date,
        (Fairy("芙蘭", Schedule.DAY, True, False),),
    )


def test_job_polls_until_today_is_available_and_broadcasts_once() -> None:
    today = date(2026, 9, 1)
    clock = MutableClock(datetime(2026, 9, 1, 13, 40, tzinfo=TAIPEI))
    ichef = FakeIChef([{}, {today: make_schedule(today)}])
    broadcaster = FakeBroadcaster()
    job = ScheduleJob(
        ichef,  # type: ignore[arg-type]
        broadcaster,  # type: ignore[arg-type]
        TAIPEI,
        clock=clock,
        sleeper=clock.sleep,
    )

    job.run()

    assert ichef.calls == 2
    assert len(broadcaster.calls) == 1
    assert broadcaster.calls[0][1] == daily_retry_key(today)
    assert "20260901 出勤的小精靈有" in broadcaster.calls[0][0]
    assert clock.current.hour == 13 and clock.current.minute == 45


def test_job_waits_until_send_window_before_fetching() -> None:
    today = date(2026, 9, 1)
    clock = MutableClock(datetime(2026, 9, 1, 13, 39, tzinfo=TAIPEI))
    ichef = FakeIChef([{today: make_schedule(today)}])
    broadcaster = FakeBroadcaster()
    job = ScheduleJob(
        ichef,  # type: ignore[arg-type]
        broadcaster,  # type: ignore[arg-type]
        TAIPEI,
        clock=clock,
        sleeper=clock.sleep,
    )

    job.run()

    assert ichef.calls == 1
    assert clock.current.hour == 13 and clock.current.minute == 40


def test_job_ignores_stale_and_future_schedules_then_fails_at_deadline() -> None:
    other_schedules = {
        date(2026, 8, 31): make_schedule(date(2026, 8, 31)),
        date(2026, 9, 2): make_schedule(date(2026, 9, 2)),
    }
    clock = MutableClock(datetime(2026, 9, 1, 14, 55, tzinfo=TAIPEI))
    ichef = FakeIChef([other_schedules, other_schedules])
    broadcaster = FakeBroadcaster()
    job = ScheduleJob(
        ichef,  # type: ignore[arg-type]
        broadcaster,  # type: ignore[arg-type]
        TAIPEI,
        clock=clock,
        sleeper=clock.sleep,
    )

    with pytest.raises(DeadlineExceededError, match="unavailable"):
        job.run()

    assert ichef.calls == 2
    assert broadcaster.calls == []
    assert clock.current.hour == 15 and clock.current.minute == 0


def test_job_rejects_empty_today_schedule() -> None:
    today = date(2026, 9, 1)
    clock = MutableClock(datetime(2026, 9, 1, 15, 0, tzinfo=TAIPEI))
    empty = DailySchedule(today, ())
    job = ScheduleJob(
        FakeIChef([{today: empty}]),  # type: ignore[arg-type]
        FakeBroadcaster(),  # type: ignore[arg-type]
        TAIPEI,
        clock=clock,
        sleeper=clock.sleep,
    )

    with pytest.raises(DeadlineExceededError):
        job.run()


def test_job_does_not_broadcast_after_deadline() -> None:
    today = date(2026, 9, 1)
    clock = MutableClock(datetime(2026, 9, 1, 15, 1, tzinfo=TAIPEI))
    ichef = FakeIChef([{today: make_schedule(today)}])
    broadcaster = FakeBroadcaster()
    job = ScheduleJob(
        ichef,  # type: ignore[arg-type]
        broadcaster,  # type: ignore[arg-type]
        TAIPEI,
        clock=clock,
        sleeper=clock.sleep,
    )

    with pytest.raises(DeadlineExceededError, match="started after"):
        job.run()

    assert ichef.calls == 0
    assert broadcaster.calls == []


def test_manual_job_broadcasts_after_deadline_with_explicit_retry_key() -> None:
    today = date(2026, 9, 1)
    clock = MutableClock(datetime(2026, 9, 1, 15, 1, tzinfo=TAIPEI))
    ichef = FakeIChef([{today: make_schedule(today)}])
    broadcaster = FakeBroadcaster()
    job = ScheduleJob(
        ichef,  # type: ignore[arg-type]
        broadcaster,  # type: ignore[arg-type]
        TAIPEI,
        clock=clock,
        sleeper=clock.sleep,
    )

    result = job.run_manual(today, "123e4567-e89b-12d3-a456-426614174000")

    assert result.already_sent is False
    assert ichef.calls == 1
    assert len(broadcaster.calls) == 1
    assert broadcaster.calls[0][1] == "123e4567-e89b-12d3-a456-426614174000"


def test_manual_job_logs_fetch_format_and_completion(
    caplog: pytest.LogCaptureFixture,
) -> None:
    today = date(2026, 9, 1)
    ichef = FakeIChef([{today: make_schedule(today)}])
    broadcaster = FakeBroadcaster()
    job = ScheduleJob(
        ichef,  # type: ignore[arg-type]
        broadcaster,  # type: ignore[arg-type]
        TAIPEI,
    )

    with caplog.at_level(logging.INFO):
        job.run_manual(today, "123e4567-e89b-12d3-a456-426614174000")

    events = [record.event for record in caplog.records]  # type: ignore[attr-defined]
    assert events == [
        "manual_started",
        "schedule_fetch_started",
        "schedule_fetch_completed",
        "schedule_formatted",
        "broadcast_sent",
        "manual_completed",
    ]
    fetch_completed = caplog.records[2]
    assert fetch_completed.target_found is True  # type: ignore[attr-defined]
    assert fetch_completed.target_fairy_count == 1  # type: ignore[attr-defined]
    formatted = caplog.records[3]
    assert formatted.message_utf16_code_units > 0  # type: ignore[attr-defined]
    completed = caplog.records[-1]
    assert (
        completed.retry_key == "123e4567-e89b-12d3-a456-426614174000"
    )  # type: ignore[attr-defined]
    assert completed.duration_ms >= 0  # type: ignore[attr-defined]


def test_manual_job_fetches_immediately_before_automatic_start_time() -> None:
    today = date(2026, 9, 1)
    clock = MutableClock(datetime(2026, 9, 1, 13, 0, tzinfo=TAIPEI))
    ichef = FakeIChef([{today: make_schedule(today)}])
    broadcaster = FakeBroadcaster()
    job = ScheduleJob(
        ichef,  # type: ignore[arg-type]
        broadcaster,  # type: ignore[arg-type]
        TAIPEI,
        clock=clock,
        sleeper=lambda _: pytest.fail("manual mode must not wait"),
    )

    job.run_manual(today)

    assert ichef.calls == 1
    assert len(broadcaster.calls) == 1


def test_manual_job_generates_a_new_retry_key_for_each_run() -> None:
    today = date(2026, 9, 1)
    ichef = FakeIChef(
        [{today: make_schedule(today)}, {today: make_schedule(today)}]
    )
    broadcaster = FakeBroadcaster()
    job = ScheduleJob(
        ichef,  # type: ignore[arg-type]
        broadcaster,  # type: ignore[arg-type]
        TAIPEI,
    )

    job.run_manual(today)
    job.run_manual(today)

    assert len(broadcaster.calls) == 2
    assert broadcaster.calls[0][1] != broadcaster.calls[1][1]
    assert all(len(key) == 36 for _, key in broadcaster.calls)


@pytest.mark.parametrize(
    "schedules",
    [
        {},
        {date(2026, 9, 1): DailySchedule(date(2026, 9, 1), ())},
    ],
)
def test_manual_job_does_not_broadcast_when_target_schedule_is_unavailable(
    schedules: dict[date, DailySchedule],
) -> None:
    today = date(2026, 9, 1)
    ichef = FakeIChef([schedules])
    broadcaster = FakeBroadcaster()
    job = ScheduleJob(
        ichef,  # type: ignore[arg-type]
        broadcaster,  # type: ignore[arg-type]
        TAIPEI,
    )

    with pytest.raises(ScheduleUnavailableError, match="2026-09-01"):
        job.run_manual(today)

    assert ichef.calls == 1
    assert broadcaster.calls == []


def test_job_fails_immediately_on_invalid_menu_shape() -> None:
    clock = MutableClock(datetime(2026, 9, 1, 13, 40, tzinfo=TAIPEI))
    job = ScheduleJob(
        FakeIChef([MenuDataError("bad shape")]),  # type: ignore[arg-type]
        FakeBroadcaster(),  # type: ignore[arg-type]
        TAIPEI,
        clock=clock,
        sleeper=clock.sleep,
    )

    with pytest.raises(MenuDataError, match="bad shape"):
        job.run()


def test_daily_retry_key_is_stable_and_changes_by_date() -> None:
    first = daily_retry_key(date(2026, 9, 1))

    assert first == daily_retry_key(date(2026, 9, 1))
    assert first != daily_retry_key(date(2026, 9, 2))
