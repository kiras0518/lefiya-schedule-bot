import logging
import os
import signal
import subprocess
import sys
from datetime import date, datetime, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from conftest import StubResponse, StubSession, menu_hours_response

from lefiya_schedule_bot import __main__ as cli
from lefiya_schedule_bot import service
from lefiya_schedule_bot.ichef import IChefClient
from lefiya_schedule_bot.job import DeadlineExceededError, ScheduleJob
from lefiya_schedule_bot.line import LineBroadcaster
from lefiya_schedule_bot.locking import job_lock
from lefiya_schedule_bot.models import (
    DailySchedule,
    Fairy,
    Schedule,
    parse_daily_schedules,
)
from lefiya_schedule_bot.scheduler import next_run, scheduler_loop

TAIPEI = ZoneInfo("Asia/Taipei")
DAY = date(2026, 9, 7)
SCHEDULE = DailySchedule(DAY, (Fairy("測試", Schedule.DAY, False, False),))


@pytest.mark.parametrize("zone", ["Asia/Taipei", "America/New_York"])
@pytest.mark.parametrize(
    "hour,minute,day,out_hour,out_minute",
    [
        (13, 34, 7, 13, 35),
        (13, 35, 7, 13, 35),
        (13, 36, 7, 13, 36),
        (14, 0, 7, 14, 0),
        (15, 0, 8, 13, 35),
        (23, 59, 8, 13, 35),
    ],
)
def test_next_run(zone, hour, minute, day, out_hour, out_minute):
    tz = ZoneInfo(zone)
    assert next_run(datetime(2026, 9, 7, hour, minute, tzinfo=tz)) == datetime(
        2026, 9, day, out_hour, out_minute, tzinfo=tz
    )


@pytest.mark.parametrize("status", [0, 1])
def test_scheduler_advances_after_success_or_failure(status, caplog):
    now = datetime(2026, 9, 7, 14, tzinfo=TAIPEI)
    calls = []

    def runner():
        calls.append(now)
        return status

    def sleep(seconds):
        assert seconds == 60
        raise InterruptedError

    with caplog.at_level(logging.INFO), pytest.raises(InterruptedError):
        scheduler_loop(TAIPEI, clock=lambda: now, sleeper=sleep, runner=runner)
    assert calls == [now]
    waiting = next(r for r in caplog.records if r.event == "scheduler_waiting")
    assert waiting.next_run == "2026-09-08T13:35:00+08:00"


def test_lock_contention_and_release(tmp_path):
    path = str(tmp_path / "job.lock")
    with job_lock(path) as first:
        assert first
        with job_lock(path) as second:
            assert not second
    with job_lock(path) as third:
        assert third


@pytest.mark.parametrize("args", [[], ["--manual"]])
def test_cli_contention_skips_clients(tmp_path, monkeypatch, args):
    path = str(tmp_path / "job.lock")
    monkeypatch.setenv("JOB_LOCK_PATH", path)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test")
    client = Mock(side_effect=AssertionError("must not fetch"))
    monkeypatch.setattr(cli, "IChefClient", client)
    with job_lock(path):
        assert cli.main(args) == 0
    client.assert_not_called()


def test_dry_run_without_token_ignores_lock(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    path = str(tmp_path / "job.lock")
    monkeypatch.setenv("JOB_LOCK_PATH", path)
    client = Mock()
    client.fetch_schedules.return_value = {DAY: SCHEDULE}
    monkeypatch.setattr(cli, "IChefClient", lambda _: client)
    with job_lock(path):
        assert cli.main(["--dry-run", "--date", DAY.isoformat()]) == 0
    output = capsys.readouterr()
    assert output.out.startswith("20260907 出勤的小精靈有")
    assert '"event": "dry_run_completed"' in output.err
    client.fetch_schedules.assert_called_once_with(DAY)


@pytest.mark.parametrize(
    "args",
    [
        ["--dry-run", "--manual"],
        ["--dry-run", "--retry-key", "bad"],
        ["--dry-run", "--date", "2026-9-7"],
    ],
)
def test_dry_run_argument_errors(args):
    assert cli.main(args) == 2


@pytest.mark.parametrize(
    "schedule",
    [
        None,
        DailySchedule(DAY, ()),
        DailySchedule(DAY, (Fairy("😀" * 2501, Schedule.DAY, False, False),)),
    ],
)
def test_dry_run_invalid_schedules(monkeypatch, capsys, schedule):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    client = Mock()
    client.fetch_schedules.return_value = {} if schedule is None else {DAY: schedule}
    monkeypatch.setattr(cli, "IChefClient", lambda _: client)
    assert cli.main(["--dry-run", "--date", DAY.isoformat()]) == 1
    assert capsys.readouterr().out == ""


def test_mixed_date_metadata_and_warning(caplog):
    categories = [
        {"name": "20260907 午安", "menuItemSnapshot": [{"name": "A"}]},
        {"name": "20260906 晚安", "menuItemSnapshot": [{"name": "B"}]},
    ]
    payload = {"data": {"restaurant": {"menu": {"categoriesSnapshot": categories}}}}
    result = parse_daily_schedules(payload, target_date=DAY)[DAY]
    assert len(result.fairies) == 2
    assert dict(result.source_counts) == {DAY: 1, date(2026, 9, 6): 1}
    assert caplog.records[-1].event == "mixed_schedule_dates"


def test_slow_fetch_cannot_broadcast():
    now = datetime(2026, 9, 7, 14, 59, tzinfo=TAIPEI)
    client, broadcaster = Mock(), Mock()

    def fetch(_):
        nonlocal now
        now += timedelta(minutes=1)
        return {DAY: SCHEDULE}

    client.fetch_schedules.side_effect = fetch
    job = ScheduleJob(client, broadcaster, TAIPEI, clock=lambda: now)
    with pytest.raises(DeadlineExceededError):
        job.run()
    broadcaster.broadcast.assert_not_called()


@pytest.mark.parametrize("kind", ["ichef", "line"])
def test_http_retry_checks_deadline(kind):
    session = Mock()
    session.post.return_value.status_code = 503
    client = (
        IChefClient("test", session=session, sleeper=lambda _: None)
        if kind == "ichef"
        else LineBroadcaster("test", session=session, sleeper=lambda _: None)
    )
    client.before_request = Mock(side_effect=[None, DeadlineExceededError()])
    with pytest.raises(DeadlineExceededError):
        if kind == "ichef":
            client.fetch_schedules(DAY)
        else:
            client.broadcast("test", "key")
    assert session.post.call_count == 1


def test_service_missing_configuration_fails_before_spawn(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    supervisor = Mock()
    monkeypatch.setattr(service, "supervise", supervisor)
    assert service.main() == 1
    supervisor.assert_not_called()


@pytest.mark.parametrize("dead_child", ["scheduler", "webhook"])
def test_real_child_exit_stops_other_child(dead_child):
    children = []

    def spawn(command, **kwargs):
        child = subprocess.Popen(command, **kwargs)
        children.append(child)
        return child

    commands = {
        name: [
            sys.executable,
            "-c",
            "pass" if name == dead_child else "import time; time.sleep(60)",
        ]
        for name in ("scheduler", "webhook")
    }
    assert service.supervise(commands, popen=spawn, grace_seconds=0.2) == 1
    assert all(child.poll() is not None for child in children)


def test_supervisor_forwards_shutdown_signal():
    children = []
    sent = False

    def spawn(command, **kwargs):
        child = subprocess.Popen(command, **kwargs)
        children.append(child)
        return child

    def sleep(_):
        nonlocal sent
        if not sent:
            sent = True
            os.kill(os.getpid(), signal.SIGTERM)

    assert (
        service.supervise(
            {"scheduler": [sys.executable, "-c", "import time; time.sleep(60)"]},
            popen=spawn,
            sleeper=sleep,
            grace_seconds=0.1,
        )
        == 0
    )
    assert children[0].poll() is not None


def test_second_graphql_request_cannot_start_after_deadline():
    session = StubSession([StubResponse(200, menu_hours_response("id"))])
    client = IChefClient("test", session=session)
    client.before_request = Mock(side_effect=[None, DeadlineExceededError()])
    with pytest.raises(DeadlineExceededError):
        client.fetch_schedules(DAY)
    assert len(session.calls) == 1


def test_inflight_line_success_can_complete_after_deadline():
    now = datetime(2026, 9, 7, 14, 59, tzinfo=TAIPEI)
    session = Mock()

    def post(*args, **kwargs):
        nonlocal now
        now += timedelta(minutes=2)
        return StubResponse(200)

    session.post.side_effect = post
    client = Mock()
    client.fetch_schedules.return_value = {DAY: SCHEDULE}
    job = ScheduleJob(
        client, LineBroadcaster("test", session=session), TAIPEI, clock=lambda: now
    )
    job.run()
    assert session.post.call_count == 1


def test_dry_run_rejects_future_without_client(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    client = Mock()
    monkeypatch.setattr(cli, "IChefClient", client)
    assert cli.main(["--dry-run", "--date", "2099-01-01"]) == 2
    client.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("LINE_CHANNEL_SECRET", ""),
        ("APP_TIMEZONE", "bad/timezone"),
        ("JOB_LOCK_PATH", ""),
    ],
)
def test_service_invalid_config_never_spawns(monkeypatch, field, value):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test")
    monkeypatch.setenv(field, value)
    supervisor = Mock()
    monkeypatch.setattr(service, "supervise", supervisor)
    assert service.main() == 1
    supervisor.assert_not_called()


def test_partial_spawn_failure_cleans_up_first_child():
    children = []

    def spawn(command, **kwargs):
        if children:
            raise OSError("test spawn failure")
        child = subprocess.Popen(command, **kwargs)
        children.append(child)
        return child

    with pytest.raises(OSError, match="test spawn failure"):
        service.supervise(
            {
                "scheduler": [sys.executable, "-c", "import time; time.sleep(60)"],
                "webhook": ["unused"],
            },
            popen=spawn,
            grace_seconds=0.1,
        )
    assert children[0].poll() is not None


def test_subprocess_lock_contention(tmp_path):
    path = str(tmp_path / "lock")
    code = (
        "from lefiya_schedule_bot.locking import job_lock; import sys\n"
        "with job_lock(sys.argv[1]) as acquired:\n"
        "    sys.exit(1 if acquired else 0)\n"
    )
    env = {**os.environ, "PYTHONPATH": "src"}
    with job_lock(path):
        result = subprocess.run(
            [sys.executable, "-c", code, path], env=env, timeout=5, check=False
        )
    assert result.returncode == 0


def test_supervisor_forces_entire_group_after_ten_seconds(monkeypatch):
    child = Mock(pid=12345)
    child.poll.return_value = 0  # Leader exited, but its descendants remain.
    clock = [0.0]
    signals = []
    monkeypatch.setattr(
        service.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )

    def sleep(seconds):
        clock[0] += seconds

    assert (
        service.supervise(
            {"scheduler": ["mock"]},
            popen=lambda *a, **kw: child,
            sleeper=sleep,
            monotonic=lambda: clock[0],
        )
        == 1
    )
    assert 10 <= clock[0] < 10.2
    assert signals[0] == (12345, signal.SIGTERM)
    assert signals[-1] == (12345, signal.SIGKILL)
    child.wait.assert_called_once()
