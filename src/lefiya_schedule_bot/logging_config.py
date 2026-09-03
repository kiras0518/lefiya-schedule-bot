from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

_STANDARD_RECORD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", record.getMessage())
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "event": event,
            "message": record.getMessage(),
            "logger": record.name,
            "process_id": record.process,
            "process_name": record.processName,
            "thread_name": record.threadName,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and key != "event":
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._lefiya_schedule_handler = True  # type: ignore[attr-defined]
    root = logging.getLogger()
    for existing in root.handlers[:]:
        if getattr(existing, "_lefiya_schedule_handler", False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)


def duration_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 1)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    logger.log(level, event, extra={"event": event, **fields})
