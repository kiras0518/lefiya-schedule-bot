from __future__ import annotations

from datetime import date

import pytest

from lefiya_schedule_bot import __main__ as main_module
from lefiya_schedule_bot.config import Settings


def test_parse_manual_arguments() -> None:
    args = main_module._parse_args(
        [
            "--manual",
            "--date",
            "2026-09-02",
            "--retry-key",
            "123E4567-E89B-12D3-A456-426614174000",
        ]
    )

    assert args.manual is True
    assert args.service_date == date(2026, 9, 2)
    assert args.retry_key == "123e4567-e89b-12d3-a456-426614174000"


@pytest.mark.parametrize(
    "argv",
    [
        ["--date", "2026-09-02"],
        ["--retry-key", "123e4567-e89b-12d3-a456-426614174000"],
        ["--manual", "--date", "2026-9-2"],
        ["--manual", "--retry-key", "not-a-uuid"],
    ],
)
def test_parse_rejects_invalid_manual_argument_combinations(
    argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        main_module._parse_args(argv)

    assert error.value.code == 2


def test_main_rejects_future_manual_date_before_creating_ichef_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSettings:
        ichef_public_id = "public-id"
        line_channel_access_token = "token"
        log_level = "INFO"
        timezone_name = "Asia/Taipei"

        @property
        def timezone(self):
            from zoneinfo import ZoneInfo

            return ZoneInfo(self.timezone_name)

    def unexpected_ichef_client(*args: object, **kwargs: object) -> object:
        raise AssertionError("future dates must not create an iCHEF client")

    monkeypatch.setattr(main_module.Settings, "from_env", lambda: FakeSettings())
    monkeypatch.setattr(main_module, "IChefClient", unexpected_ichef_client)

    assert main_module.main(["--manual", "--date", "2099-01-01"]) == 2


def test_main_dispatches_manual_mode_with_explicit_target_and_retry_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[date, str | None]] = []

    class FakeJob:
        def __init__(self, *_args: object) -> None:
            pass

        def run_manual(self, target_date: date, retry_key: str | None) -> None:
            calls.append((target_date, retry_key))

        def run(self) -> None:
            raise AssertionError("manual mode must not call automatic run")

    settings = Settings("token", timezone_name="Asia/Taipei")
    monkeypatch.setattr(main_module.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(main_module, "IChefClient", lambda _public_id: object())
    monkeypatch.setattr(main_module, "LineBroadcaster", lambda _token: object())
    monkeypatch.setattr(main_module, "ScheduleJob", FakeJob)

    assert (
        main_module.main(
            [
                "--manual",
                "--date",
                "2026-09-01",
                "--retry-key",
                "123e4567-e89b-12d3-a456-426614174000",
            ]
        )
        == 0
    )

    assert calls == [
        (date(2026, 9, 1), "123e4567-e89b-12d3-a456-426614174000")
    ]
