from __future__ import annotations

import argparse
import logging
import uuid
from collections.abc import Sequence
from datetime import date, datetime

from .config import ConfigurationError, Settings
from .ichef import IChefClient
from .job import ScheduleJob
from .line import LineBroadcaster
from .logging_config import configure_logging, log_event


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch and broadcast Lefiya's daily schedule."
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="fetch once immediately and allow dates after the automatic deadline",
    )
    parser.add_argument(
        "--date",
        dest="service_date_text",
        help="manual target date in YYYY-MM-DD format; defaults to today",
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

    if not args.manual and (
        args.service_date_text is not None or args.retry_key is not None
    ):
        parser.error("--date and --retry-key require --manual")

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
    configure_logging("INFO")
    logger = logging.getLogger(__name__)

    try:
        args = _parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    try:
        settings = Settings.from_env()
        configure_logging(settings.log_level)
        target_date: date | None = None
        if args.manual:
            current_date = datetime.now(settings.timezone).date()
            target_date = args.service_date or current_date
            if target_date > current_date:
                log_event(
                    logger,
                    logging.ERROR,
                    "invalid_arguments",
                    error="manual target date cannot be in the future",
                    schedule_date=target_date.strftime("%Y%m%d"),
                )
                return 2
        job = ScheduleJob(
            IChefClient(settings.ichef_public_id),
            LineBroadcaster(settings.line_channel_access_token),
            settings.timezone,
        )
        if target_date is not None:
            job.run_manual(target_date, args.retry_key)
        else:
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
