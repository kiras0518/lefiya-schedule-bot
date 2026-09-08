"""Calendar-based daily scheduling."""

import logging
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as wall_time

from .config import Settings
from .logging_config import configure_logging, log_event


def next_run(now: datetime, last_run: date | None = None) -> datetime:
    day = now.date()
    if last_run is not None and day <= last_run:
        day = last_run + timedelta(days=1)
    elif now.time() >= wall_time(15):
        day += timedelta(days=1)
    return max(now, datetime.combine(day, wall_time(13, 35), now.tzinfo))


def scheduler_loop(timezone, *, clock=None, sleeper=time.sleep, runner=None):
    clock = clock or (lambda: datetime.now(timezone))
    runner = runner or (
        lambda: subprocess.call([sys.executable, "-m", "lefiya_schedule_bot"])
    )
    logger = logging.getLogger(__name__)
    log_event(logger, logging.INFO, "scheduler_started", timezone=timezone.key)
    last_run = None
    announced = None
    while True:
        now = clock().astimezone(timezone)
        target = next_run(now, last_run)
        wait = max(0, target.timestamp() - now.timestamp())
        if wait:
            if target != announced:
                log_event(
                    logger,
                    logging.INFO,
                    "scheduler_waiting",
                    timezone=timezone.key,
                    next_run=target.isoformat(),
                    wait_seconds=wait,
                )
                announced = target
            sleeper(min(wait, 60))
            continue
        last_run = now.date()
        log_event(
            logger,
            logging.INFO,
            "scheduler_catchup_started",
            schedule_date=last_run.isoformat(),
        )
        log_event(
            logger,
            logging.INFO,
            "scheduler_job_started",
            schedule_date=last_run.isoformat(),
        )
        status = runner()
        log_event(
            logger,
            logging.ERROR if status else logging.INFO,
            "scheduler_job_failed" if status else "scheduler_job_completed",
            schedule_date=last_run.isoformat(),
            exit_code=status,
        )


if __name__ == "__main__":
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    scheduler_loop(settings.timezone)
