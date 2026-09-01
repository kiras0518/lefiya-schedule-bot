from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    line_channel_access_token: str
    ichef_public_id: str = "WqxdHUPa"
    timezone_name: str = "Asia/Taipei"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
        if not token:
            raise ConfigurationError("LINE_CHANNEL_ACCESS_TOKEN is required")

        public_id = os.environ.get("ICHEF_PUBLIC_ID", "WqxdHUPa").strip()
        if not public_id:
            raise ConfigurationError("ICHEF_PUBLIC_ID cannot be empty")

        timezone_name = os.environ.get("APP_TIMEZONE", "Asia/Taipei").strip()
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ConfigurationError(
                f"APP_TIMEZONE is not a valid IANA timezone: {timezone_name}"
            ) from error

        log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in logging.getLevelNamesMapping():
            raise ConfigurationError(f"LOG_LEVEL is invalid: {log_level}")

        return cls(
            line_channel_access_token=token,
            ichef_public_id=public_id,
            timezone_name=timezone_name,
            log_level=log_level,
        )

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)
