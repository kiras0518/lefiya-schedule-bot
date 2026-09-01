from __future__ import annotations

import logging

from .config import ConfigurationError, Settings
from .ichef import IChefClient
from .job import ScheduleJob
from .line import LineBroadcaster
from .logging_config import configure_logging, log_event


def main() -> int:
    configure_logging("INFO")
    logger = logging.getLogger(__name__)
    try:
        settings = Settings.from_env()
        configure_logging(settings.log_level)
        job = ScheduleJob(
            IChefClient(settings.ichef_public_id),
            LineBroadcaster(settings.line_channel_access_token),
            settings.timezone,
        )
        job.run()
        return 0
    except ConfigurationError as error:
        log_event(logger, logging.ERROR, "configuration_error", error=str(error))
        return 2
    except Exception as error:
        log_event(
            logger,
            logging.ERROR,
            "job_failed",
            error=str(error),
            error_type=type(error).__name__,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
