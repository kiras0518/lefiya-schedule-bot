from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

import requests

from .logging_config import duration_ms, log_event


class LineAPIError(RuntimeError):
    """Raised when LINE rejects or repeatedly fails a broadcast request."""


class MessageTooLongError(LineAPIError):
    """Raised when a LINE text message exceeds its UTF-16 limit."""


@dataclass(frozen=True)
class BroadcastResult:
    already_sent: bool
    request_id: str | None


class LineBroadcaster:
    BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
    MAX_TEXT_CODE_UNITS = 5_000

    def __init__(
        self,
        channel_access_token: str,
        *,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
        timeout: tuple[float, float] = (5.0, 20.0),
        logger: logging.Logger | None = None,
    ) -> None:
        self.channel_access_token = channel_access_token
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self.max_attempts = max_attempts
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)

    def broadcast(self, text: str, retry_key: str) -> BroadcastResult:
        started_at = perf_counter()
        code_units = utf16_code_units(text)
        log_event(
            self.logger,
            logging.INFO,
            "line_broadcast_started",
            retry_key=retry_key,
            message_length=len(text),
            message_utf16_code_units=code_units,
            max_attempts=self.max_attempts,
        )
        if code_units > self.MAX_TEXT_CODE_UNITS:
            log_event(
                self.logger,
                logging.ERROR,
                "line_broadcast_failed",
                retry_key=retry_key,
                error=(
                    f"LINE text has {code_units} UTF-16 code units; "
                    "maximum is 5000"
                ),
                error_type=MessageTooLongError.__name__,
                duration_ms=duration_ms(started_at),
            )
            raise MessageTooLongError(
                f"LINE text has {code_units} UTF-16 code units; maximum is 5000"
            )

        headers = {
            "Authorization": f"Bearer {self.channel_access_token}",
            "Content-Type": "application/json",
            "X-Line-Retry-Key": retry_key,
        }
        payload = {"messages": [{"type": "text", "text": text}]}
        last_error: Exception | None = None
        last_status_code: int | None = None

        for attempt in range(1, self.max_attempts + 1):
            attempt_started_at = perf_counter()
            log_event(
                self.logger,
                logging.INFO,
                "line_request_started",
                retry_key=retry_key,
                attempt=attempt,
                max_attempts=self.max_attempts,
            )
            try:
                response = self.session.post(
                    self.BROADCAST_URL,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = error
                log_event(
                    self.logger,
                    logging.WARNING,
                    "line_request_error",
                    retry_key=retry_key,
                    attempt=attempt,
                    max_attempts=self.max_attempts,
                    error=str(error),
                    error_type=type(error).__name__,
                    duration_ms=duration_ms(attempt_started_at),
                )
                if attempt < self.max_attempts:
                    retry_in_seconds = _retry_delay(attempt)
                    log_event(
                        self.logger,
                        logging.INFO,
                        "line_request_retrying",
                        retry_key=retry_key,
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        retry_in_seconds=retry_in_seconds,
                        reason="request_exception",
                    )
                    self.sleeper(retry_in_seconds)
                    continue
                break

            last_status_code = response.status_code
            request_id = response.headers.get("X-Line-Request-Id") or (
                response.headers.get("X-Line-Accepted-Request-Id")
            )
            log_event(
                self.logger,
                logging.INFO,
                "line_response_received",
                retry_key=retry_key,
                attempt=attempt,
                status_code=response.status_code,
                request_id=request_id,
                duration_ms=duration_ms(attempt_started_at),
            )

            if 200 <= response.status_code < 300:
                log_event(
                    self.logger,
                    logging.INFO,
                    "line_broadcast_succeeded",
                    retry_key=retry_key,
                    attempt=attempt,
                    status_code=response.status_code,
                    request_id=request_id,
                    duration_ms=duration_ms(started_at),
                )
                return BroadcastResult(
                    already_sent=False,
                    request_id=request_id,
                )

            if response.status_code == 409:
                log_event(
                    self.logger,
                    logging.INFO,
                    "line_broadcast_already_sent",
                    retry_key=retry_key,
                    attempt=attempt,
                    status_code=response.status_code,
                    request_id=request_id,
                    duration_ms=duration_ms(started_at),
                )
                return BroadcastResult(
                    already_sent=True,
                    request_id=request_id,
                )

            if response.status_code == 429 or response.status_code >= 500:
                last_error = LineAPIError(
                    f"LINE returned retryable HTTP {response.status_code}"
                )
                if attempt < self.max_attempts:
                    retry_in_seconds = _retry_delay(attempt)
                    log_event(
                        self.logger,
                        logging.INFO,
                        "line_request_retrying",
                        retry_key=retry_key,
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        retry_in_seconds=retry_in_seconds,
                        reason=f"http_{response.status_code}",
                    )
                    self.sleeper(retry_in_seconds)
                    continue
                break

            error_message = (
                f"LINE returned non-retryable HTTP {response.status_code}: "
                f"{_safe_response_message(response)}"
            )
            log_event(
                self.logger,
                logging.ERROR,
                "line_broadcast_failed",
                retry_key=retry_key,
                attempt=attempt,
                status_code=response.status_code,
                error=error_message,
                error_type=LineAPIError.__name__,
                duration_ms=duration_ms(started_at),
            )
            raise LineAPIError(error_message)

        log_event(
            self.logger,
            logging.ERROR,
            "line_broadcast_failed",
            retry_key=retry_key,
            attempt=self.max_attempts,
            max_attempts=self.max_attempts,
            status_code=last_status_code,
            error="LINE broadcast failed after retries",
            error_type=type(last_error).__name__ if last_error else "UnknownError",
            last_error=str(last_error) if last_error else None,
            duration_ms=duration_ms(started_at),
        )
        raise LineAPIError("LINE broadcast failed after retries") from last_error


def utf16_code_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _retry_delay(attempt: int) -> float:
    return min(2 ** (attempt - 1), 4)


def _safe_response_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except (requests.JSONDecodeError, ValueError):
        return "non-JSON error response"
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return body["message"][:300]
    return "error response without a message"
