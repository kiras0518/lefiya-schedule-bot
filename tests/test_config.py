from __future__ import annotations

import pytest

from lefiya_schedule_bot.config import ConfigurationError, Settings


def test_settings_read_required_and_default_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", " secret-token ")
    monkeypatch.delenv("ICHEF_PUBLIC_ID", raising=False)
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    settings = Settings.from_env()

    assert settings.line_channel_access_token == "secret-token"
    assert settings.ichef_public_id == "WqxdHUPa"
    assert settings.timezone.key == "Asia/Taipei"
    assert settings.log_level == "INFO"


def test_settings_require_line_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)

    with pytest.raises(ConfigurationError, match="LINE_CHANNEL_ACCESS_TOKEN"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("APP_TIMEZONE", "Not/A_Zone", "valid IANA timezone"),
        ("LOG_LEVEL", "LOUD", "LOG_LEVEL is invalid"),
    ],
)
def test_settings_reject_invalid_optional_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError, match=message):
        Settings.from_env()
