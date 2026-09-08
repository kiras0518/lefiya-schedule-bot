from __future__ import annotations

import argparse
import logging
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from time import perf_counter

from .config import ConfigurationError, Settings
from .formatter import format_schedule_message
from .ichef import IChefClient
from .job import ScheduleJob, ScheduleUnavailableError
from .line import LineBroadcaster, MessageTooLongError, utf16_code_units
from .locking import job_lock
from .logging_config import configure_logging, duration_ms, log_event


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch and broadcast Lefiya's daily schedule."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="preview once without calling LINE"
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="fetch once immediately and allow dates after the automatic deadline",
    )
    parser.add_argument(
        "--date",
        dest="service_date_text",
        help="manual or dry-run target date in YYYY-MM-DD; defaults to today",
    )
    parser.add_argument(
        "--retry-key",
        help="manual LINE retry UUID; reuse it when a previous result is uncertain",
    )
    return parser


def _parse_date(parser: argparse.ArgumentParser, value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        parser.error(f"--date must use YYYY-MM-DD format: {value}")
        raise AssertionError("argparse.error must raise") from error
    if parsed.isoformat() != value:
        parser.error(f"--date must use YYYY-MM-DD format: {value}")
    return parsed


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.dry_run and (args.manual or args.retry_key):
        parser.error("--dry-run cannot be combined with --manual or --retry-key")
    if args.service_date_text and not (args.manual or args.dry_run):
        parser.error("--date requires --manual or --dry-run")
    if args.retry_key and not args.manual:
        parser.error("--retry-key requires --manual")

    if args.service_date_text is not None:
        args.service_date = _parse_date(parser, args.service_date_text)
    else:
        args.service_date = None

    if args.retry_key is not None:
        try:
            args.retry_key = str(uuid.UUID(args.retry_key))
        except ValueError as error:
            parser.error("--retry-key must be a valid UUID")
            raise AssertionError("argparse.error must raise") from error

    return args


def main(argv: Sequence[str] | None = None) -> int:
    started_at = perf_counter()
    configure_logging("INFO")
    logger = logging.getLogger(__name__)

    try:
        args = _parse_args(argv)
    except SystemExit as error:
        exit_code = int(error.code) if isinstance(error.code, int) else 2
        log_event(
            logger,
            logging.INFO if exit_code == 0 else logging.ERROR,
            "cli_completed" if exit_code == 0 else "cli_failed",
            error=(
                "help requested" if exit_code == 0 else "invalid command-line arguments"
            ),
            error_type=(None if exit_code == 0 else "ArgumentError"),
            exit_code=exit_code,
            duration_ms=duration_ms(started_at),
        )
        return exit_code

    mode = "dry_run" if args.dry_run else "manual" if args.manual else "automatic"
    target_date: date | None = args.service_date if args.manual else None
    retry_key: str | None = args.retry_key if args.manual else None
    log_event(
        logger,
        logging.INFO,
        "cli_started",
        mode=mode,
        schedule_date=(
            target_date.strftime("%Y%m%d") if target_date is not None else None
        ),
        retry_key=retry_key,
    )
    try:
        settings = (
            Settings.from_env(require_token=False)
            if args.dry_run
            else Settings.from_env()
        )
        configure_logging(settings.log_level)
        log_event(
            logger,
            logging.INFO,
            "settings_loaded",
            mode=mode,
            timezone=settings.timezone_name,
            log_level=settings.log_level,
        )
        if args.manual or args.dry_run:
            current_date = datetime.now(settings.timezone).date()
            target_date = args.service_date or current_date
            log_event(
                logger,
                logging.INFO,
                "target_date_resolved",
                mode=mode,
                schedule_date=target_date.strftime("%Y%m%d"),
                timezone=settings.timezone_name,
                date_source=("argument" if args.service_date is not None else "today"),
            )
            if target_date > current_date:
                log_event(
                    logger,
                    logging.ERROR,
                    "cli_failed",
                    mode=mode,
                    error="target date cannot be in the future",
                    schedule_date=target_date.strftime("%Y%m%d"),
                    timezone=settings.timezone_name,
                    exit_code=2,
                    duration_ms=duration_ms(started_at),
                )
                return 2
        if args.dry_run:
            schedules = IChefClient(settings.ichef_public_id).fetch_schedules(
                target_date
            )
            schedule = schedules.get(target_date)
            if schedule is None or not schedule.fairies:
                raise ScheduleUnavailableError("requested schedule unavailable")
            message = format_schedule_message(schedule)
            length = utf16_code_units(message)
            if length > LineBroadcaster.MAX_TEXT_CODE_UNITS:
                raise MessageTooLongError("LINE text exceeds 5000 UTF-16 code units")
            log_event(
                logger,
                logging.INFO,
                "dry_run_completed",
                schedule_date=target_date.isoformat(),
                source_counts={
                    day.isoformat(): count for day, count in schedule.source_counts
                },
                fairy_count=len(schedule.fairies),
                message_utf16_code_units=length,
            )
            print(message)
            return 0
        with job_lock(settings.job_lock_path) as acquired:
            if not acquired:
                log_event(logger, logging.INFO, "job_already_running", mode=mode)
                return 0
            job = ScheduleJob(
                IChefClient(settings.ichef_public_id),
                LineBroadcaster(settings.line_channel_access_token),
                settings.timezone,
            )
            if target_date is not None:
                result = job.run_manual(target_date, args.retry_key)
            else:
                job.run()
                result = None
        log_event(
            logger,
            logging.INFO,
            "cli_completed",
            mode=mode,
            schedule_date=(
                target_date.strftime("%Y%m%d") if target_date is not None else None
            ),
            retry_key=retry_key,
            already_sent=(result.already_sent if result is not None else None),
            exit_code=0,
            duration_ms=duration_ms(started_at),
        )
        return 0
    except ConfigurationError as error:
        log_event(
            logger,
            logging.ERROR,
            "configuration_error",
            mode=mode,
            error=str(error),
            error_type=type(error).__name__,
            exit_code=2,
            duration_ms=duration_ms(started_at),
        )
        return 2
    except Exception as error:
        log_event(
            logger,
            logging.ERROR,
            "job_failed",
            mode=mode,
            schedule_date=(
                target_date.strftime("%Y%m%d") if target_date is not None else None
            ),
            retry_key=retry_key,
            error=str(error),
            error_type=type(error).__name__,
            exit_code=1,
            duration_ms=duration_ms(started_at),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
