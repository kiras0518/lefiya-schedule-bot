from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import requests


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
    ) -> None:
        self.channel_access_token = channel_access_token
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self.max_attempts = max_attempts
        self.timeout = timeout

    def broadcast(self, text: str, retry_key: str) -> BroadcastResult:
        code_units = utf16_code_units(text)
        if code_units > self.MAX_TEXT_CODE_UNITS:
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

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.post(
                    self.BROADCAST_URL,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = error
                if attempt < self.max_attempts:
                    self.sleeper(_retry_delay(attempt))
                    continue
                break

            if 200 <= response.status_code < 300:
                return BroadcastResult(
                    already_sent=False,
                    request_id=response.headers.get("X-Line-Request-Id"),
                )

            if response.status_code == 409:
                return BroadcastResult(
                    already_sent=True,
                    request_id=response.headers.get("X-Line-Accepted-Request-Id")
                    or response.headers.get("X-Line-Request-Id"),
                )

            if response.status_code == 429 or response.status_code >= 500:
                last_error = LineAPIError(
                    f"LINE returned retryable HTTP {response.status_code}"
                )
                if attempt < self.max_attempts:
                    self.sleeper(_retry_delay(attempt))
                    continue
                break

            raise LineAPIError(
                f"LINE returned non-retryable HTTP {response.status_code}: "
                f"{_safe_response_message(response)}"
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
