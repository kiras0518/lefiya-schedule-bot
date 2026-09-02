from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime
from datetime import time as time_of_day
from zoneinfo import ZoneInfo

from .formatter import format_schedule_message
from .ichef import IChefAPIError, IChefClient
from .line import BroadcastResult, LineBroadcaster
from .logging_config import log_event
from .models import DailySchedule, MenuDataError


class DeadlineExceededError(RuntimeError):
    """Raised when today's schedule is still unavailable at the deadline."""


class ScheduleUnavailableError(RuntimeError):
    """Raised when a requested service date has no non-empty schedule."""


class ScheduleJob:
    START_TIME = time_of_day(13, 40)
    DEADLINE = time_of_day(15, 0)
    POLL_INTERVAL_SECONDS = 300

    def __init__(
        self,
        ichef: IChefClient,
        broadcaster: LineBroadcaster,
        timezone: ZoneInfo,
        *,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self.ichef = ichef
        self.broadcaster = broadcaster
        self.timezone = timezone
        self.clock = clock or (lambda: datetime.now(timezone))
        self.sleeper = sleeper
        self.logger = logger or logging.getLogger(__name__)

    def run(self) -> None:
        run_date: date | None = None
        last_upstream_error: IChefAPIError | None = None

        while True:
            now = self._now()
            if run_date is None:
                run_date = now.date()
            elif now.date() != run_date:
                raise DeadlineExceededError("schedule job crossed into a new day")

            if now.time() < self.START_TIME:
                seconds = min(
                    self.POLL_INTERVAL_SECONDS,
                    _seconds_until(now, self.START_TIME),
                )
                log_event(
                    self.logger,
                    logging.INFO,
                    "waiting_for_today",
                    schedule_date=run_date.strftime("%Y%m%d"),
                    reason="before_send_window",
                    next_poll_seconds=seconds,
                )
                self.sleeper(seconds)
                continue

            if now.time() > self.DEADLINE:
                log_event(
                    self.logger,
                    logging.ERROR,
                    "deadline_exceeded",
                    schedule_date=run_date.strftime("%Y%m%d"),
                    reason="job_started_after_deadline",
                )
                raise DeadlineExceededError(
                    f"schedule job started after {self.DEADLINE:%H:%M}"
                )

            try:
                schedules = self.ichef.fetch_schedules()
                last_upstream_error = None
            except IChefAPIError as error:
                last_upstream_error = error
                log_event(
                    self.logger,
                    logging.WARNING,
                    "upstream_error",
                    schedule_date=run_date.strftime("%Y%m%d"),
                    upstream="ichef",
                    error=str(error),
                )
                schedules = {}
            except MenuDataError:
                log_event(
                    self.logger,
                    logging.ERROR,
                    "upstream_error",
                    schedule_date=run_date.strftime("%Y%m%d"),
                    upstream="ichef",
                    error="invalid_response_shape",
                )
                raise

            schedule = schedules.get(run_date)
            if schedule is not None and schedule.fairies:
                self._broadcast_schedule(
                    schedule,
                    daily_retry_key(run_date),
                    mode="automatic",
                )
                return

            if now.time() >= self.DEADLINE:
                log_event(
                    self.logger,
                    logging.ERROR,
                    "deadline_exceeded",
                    schedule_date=run_date.strftime("%Y%m%d"),
                    last_upstream_error=(
                        str(last_upstream_error) if last_upstream_error else None
                    ),
                )
                raise DeadlineExceededError(
                    f"today's schedule was unavailable by {self.DEADLINE:%H:%M}"
                )

            seconds = min(
                self.POLL_INTERVAL_SECONDS,
                _seconds_until(now, self.DEADLINE),
            )
            available_dates = sorted(
                service_date.strftime("%Y%m%d") for service_date in schedules
            )
            log_event(
                self.logger,
                logging.INFO,
                "waiting_for_today",
                schedule_date=run_date.strftime("%Y%m%d"),
                available_dates=available_dates,
                reason="today_not_available",
                next_poll_seconds=seconds,
            )
            self.sleeper(seconds)

    def run_manual(
        self,
        target_date: date,
        retry_key: str | None = None,
    ) -> BroadcastResult:
        """Fetch and broadcast one requested date without schedule time limits."""
        effective_retry_key = (
            retry_key if retry_key is not None else manual_retry_key()
        )
        schedule_date = target_date.strftime("%Y%m%d")
        log_event(
            self.logger,
            logging.INFO,
            "manual_started",
            mode="manual",
            schedule_date=schedule_date,
            retry_key=effective_retry_key,
        )

        try:
            schedules = self.ichef.fetch_schedules()
            schedule = schedules.get(target_date)
            if schedule is None or not schedule.fairies:
                raise ScheduleUnavailableError(
                    f"schedule unavailable for {target_date:%Y-%m-%d}"
                )
            return self._broadcast_schedule(
                schedule,
                effective_retry_key,
                mode="manual",
            )
        except Exception as error:
            log_event(
                self.logger,
                logging.ERROR,
                "manual_failed",
                mode="manual",
                schedule_date=schedule_date,
                retry_key=effective_retry_key,
                error=str(error),
                error_type=type(error).__name__,
            )
            raise

    def _broadcast_schedule(
        self,
        schedule: DailySchedule,
        retry_key: str,
        *,
        mode: str,
    ) -> BroadcastResult:
        message = format_schedule_message(schedule)
        result = self.broadcaster.broadcast(message, retry_key)
        event = "already_sent" if result.already_sent else "broadcast_sent"
        log_event(
            self.logger,
            logging.INFO,
            event,
            mode=mode,
            schedule_date=schedule.service_date.strftime("%Y%m%d"),
            retry_key=retry_key,
            line_request_id=result.request_id,
            fairy_count=len(schedule.fairies),
        )
        return result

    def _now(self) -> datetime:
        current = self.clock()
        if current.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return current.astimezone(self.timezone)


def daily_retry_key(service_date: date) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"https://order.lefiya.com/schedule/{service_date:%Y%m%d}",
        )
    )


def manual_retry_key() -> str:
    return str(uuid.uuid4())


def _seconds_until(now: datetime, target: time_of_day) -> float:
    target_datetime = datetime.combine(now.date(), target, tzinfo=now.tzinfo)
    return max((target_datetime - now).total_seconds(), 0.0)
