from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date
from time import perf_counter
from typing import Any

import requests

from .logging_config import duration_ms, log_event
from .models import DailySchedule, MenuDataError, parse_daily_schedules


class IChefAPIError(RuntimeError):
    """Raised after a transient iCHEF request exhausts its retries."""


class IChefClient:
    BASE_URL = "https://shop.ichefpos.com/api/graphql/online_restaurant"
    MENU_HOURS_QUERY = """query menuHoursSnapshotQuery(
  $publicId: String!
  $platformType: PlatformTypes!
) {
  restaurant(publicId: $publicId) {
    onlineOrderingMenu(platformType: $platformType) {
      menuHoursSnapshot {
        categorySnapshotUuids
      }
    }
  }
}"""
    MENU_ITEMS_QUERY = """query restaurantMenuItemCategoriesQuery(
  $publicId: String
  $categoriesSnapshotUuids: [UUID!]!
) {
  restaurant(publicId: $publicId) {
    menu {
      categoriesSnapshot(uuids: $categoriesSnapshotUuids) {
        name
        menuItemSnapshot {
          name
          modifierGroupSnapshot {
            name
            modifierOptionSnapshot {
              name
            }
          }
        }
      }
    }
  }
}"""

    def __init__(
        self,
        public_id: str,
        *,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
        timeout: tuple[float, float] = (5.0, 20.0),
        logger: logging.Logger | None = None,
    ) -> None:
        self.public_id = public_id
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self.max_attempts = max_attempts
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)

    def fetch_schedules(
        self,
        target_date: date | None = None,
    ) -> dict[date, DailySchedule]:
        started_at = perf_counter()
        log_event(
            self.logger,
            logging.INFO,
            "ichef_fetch_started",
            requested_date=(
                target_date.strftime("%Y%m%d") if target_date is not None else None
            ),
        )
        try:
            schedules, category_uuid_count = self._fetch_schedules(target_date)
        except Exception as error:
            log_event(
                self.logger,
                logging.ERROR,
                "ichef_fetch_failed",
                requested_date=(
                    target_date.strftime("%Y%m%d")
                    if target_date is not None
                    else None
                ),
                error=str(error),
                error_type=type(error).__name__,
                duration_ms=duration_ms(started_at),
            )
            raise
        log_event(
            self.logger,
            logging.INFO,
            "ichef_fetch_completed",
            requested_date=(
                target_date.strftime("%Y%m%d") if target_date is not None else None
            ),
            category_uuid_count=category_uuid_count,
            schedule_count=len(schedules),
            fairy_count=sum(len(schedule.fairies) for schedule in schedules.values()),
            available_dates=sorted(
                service_date.strftime("%Y%m%d") for service_date in schedules
            ),
            duration_ms=duration_ms(started_at),
        )
        return schedules

    def _fetch_schedules(
        self,
        target_date: date | None = None,
    ) -> tuple[dict[date, DailySchedule], int]:
        category_uuids = self._fetch_category_uuids()
        if not category_uuids:
            return {}, 0
        menu = self._request(
            operation="restaurantMenuItemCategoriesQuery",
            query=self.MENU_ITEMS_QUERY,
            variables={
                "publicId": self.public_id,
                "categoriesSnapshotUuids": category_uuids,
            },
        )
        return parse_daily_schedules(menu, target_date=target_date), len(category_uuids)

    def _fetch_category_uuids(self) -> list[str]:
        result = self._request(
            operation="menuHoursSnapshotQuery",
            query=self.MENU_HOURS_QUERY,
            variables={"publicId": self.public_id, "platformType": "ICHEF"},
        )
        try:
            snapshots = result["data"]["restaurant"]["onlineOrderingMenu"][
                "menuHoursSnapshot"
            ]
        except (KeyError, TypeError) as error:
            raise MenuDataError(
                "missing menuHoursSnapshot in iCHEF response"
            ) from error

        if not isinstance(snapshots, list):
            raise MenuDataError("menuHoursSnapshot must be a list")

        category_uuids: list[str] = []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                raise MenuDataError("menu hours snapshot must be an object")
            uuids = snapshot.get("categorySnapshotUuids")
            if not isinstance(uuids, list) or not all(
                isinstance(value, str) for value in uuids
            ):
                raise MenuDataError("categorySnapshotUuids must be a string list")
            category_uuids.extend(uuids)
        return category_uuids

    def _request(
        self,
        *,
        operation: str,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "operationName": operation,
            "variables": variables,
            "query": query,
        }
        headers = {"Content-Type": "application/json", "cache-control": "no-cache"}
        url = f"{self.BASE_URL}?op={operation}"
        last_error: Exception | None = None
        last_status_code: int | None = None

        for attempt in range(1, self.max_attempts + 1):
            started_at = perf_counter()
            log_event(
                self.logger,
                logging.INFO,
                "ichef_request_started",
                operation=operation,
                attempt=attempt,
                max_attempts=self.max_attempts,
            )
            try:
                response = self.session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = error
                log_event(
                    self.logger,
                    logging.WARNING,
                    "ichef_request_error",
                    operation=operation,
                    attempt=attempt,
                    max_attempts=self.max_attempts,
                    error=str(error),
                    error_type=type(error).__name__,
                    duration_ms=duration_ms(started_at),
                )
                if attempt < self.max_attempts:
                    retry_in_seconds = _retry_delay(attempt)
                    log_event(
                        self.logger,
                        logging.INFO,
                        "ichef_request_retrying",
                        operation=operation,
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        retry_in_seconds=retry_in_seconds,
                        reason="request_exception",
                    )
                    self.sleeper(retry_in_seconds)
                    continue
                break

            log_event(
                self.logger,
                logging.INFO,
                "ichef_response_received",
                operation=operation,
                attempt=attempt,
                status_code=response.status_code,
                duration_ms=duration_ms(started_at),
            )
            last_status_code = response.status_code

            if response.status_code == 429 or response.status_code >= 500:
                last_error = IChefAPIError(
                    f"iCHEF returned retryable HTTP {response.status_code}"
                )
                if attempt < self.max_attempts:
                    retry_in_seconds = _retry_delay(attempt)
                    log_event(
                        self.logger,
                        logging.INFO,
                        "ichef_request_retrying",
                        operation=operation,
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        retry_in_seconds=retry_in_seconds,
                        reason=f"http_{response.status_code}",
                    )
                    self.sleeper(retry_in_seconds)
                    continue
                break

            if response.status_code >= 400:
                log_event(
                    self.logger,
                    logging.ERROR,
                    "ichef_request_failed",
                    operation=operation,
                    attempt=attempt,
                    status_code=response.status_code,
                    error=f"iCHEF returned non-retryable HTTP {response.status_code}",
                    error_type="MenuDataError",
                )
                raise MenuDataError(
                    f"iCHEF returned non-retryable HTTP {response.status_code}"
                )

            try:
                body = response.json()
            except requests.JSONDecodeError as error:
                log_event(
                    self.logger,
                    logging.ERROR,
                    "ichef_request_failed",
                    operation=operation,
                    attempt=attempt,
                    status_code=response.status_code,
                    error=str(error),
                    error_type=type(error).__name__,
                )
                raise MenuDataError("iCHEF response is not valid JSON") from error

            if not isinstance(body, dict):
                log_event(
                    self.logger,
                    logging.ERROR,
                    "ichef_request_failed",
                    operation=operation,
                    attempt=attempt,
                    status_code=response.status_code,
                    error="iCHEF response must be a JSON object",
                    error_type="MenuDataError",
                )
                raise MenuDataError("iCHEF response must be a JSON object")
            if body.get("errors"):
                log_event(
                    self.logger,
                    logging.ERROR,
                    "ichef_request_failed",
                    operation=operation,
                    attempt=attempt,
                    status_code=response.status_code,
                    error="iCHEF GraphQL response contains errors",
                    error_type="MenuDataError",
                )
                raise MenuDataError("iCHEF GraphQL response contains errors")

            log_event(
                self.logger,
                logging.INFO,
                "ichef_request_succeeded",
                operation=operation,
                attempt=attempt,
                status_code=response.status_code,
                duration_ms=duration_ms(started_at),
            )
            return body

        log_event(
            self.logger,
            logging.ERROR,
            "ichef_request_failed",
            operation=operation,
            attempt=self.max_attempts,
            max_attempts=self.max_attempts,
            status_code=last_status_code,
            error="iCHEF request failed after retries",
            error_type=type(last_error).__name__ if last_error else "UnknownError",
            last_error=str(last_error) if last_error else None,
        )
        raise IChefAPIError("iCHEF request failed after retries") from last_error


def _retry_delay(attempt: int) -> float:
    return min(2 ** (attempt - 1), 4)
