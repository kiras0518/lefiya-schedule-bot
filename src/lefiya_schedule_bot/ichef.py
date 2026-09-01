from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from typing import Any

import requests

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
    ) -> None:
        self.public_id = public_id
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self.max_attempts = max_attempts
        self.timeout = timeout

    def fetch_schedules(self) -> dict[date, DailySchedule]:
        category_uuids = self._fetch_category_uuids()
        if not category_uuids:
            return {}
        menu = self._request(
            operation="restaurantMenuItemCategoriesQuery",
            query=self.MENU_ITEMS_QUERY,
            variables={
                "publicId": self.public_id,
                "categoriesSnapshotUuids": category_uuids,
            },
        )
        return parse_daily_schedules(menu)

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

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.post(
                    url,
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

            if response.status_code == 429 or response.status_code >= 500:
                last_error = IChefAPIError(
                    f"iCHEF returned retryable HTTP {response.status_code}"
                )
                if attempt < self.max_attempts:
                    self.sleeper(_retry_delay(attempt))
                    continue
                break

            if response.status_code >= 400:
                raise MenuDataError(
                    f"iCHEF returned non-retryable HTTP {response.status_code}"
                )

            try:
                body = response.json()
            except requests.JSONDecodeError as error:
                raise MenuDataError("iCHEF response is not valid JSON") from error

            if not isinstance(body, dict):
                raise MenuDataError("iCHEF response must be a JSON object")
            if body.get("errors"):
                raise MenuDataError("iCHEF GraphQL response contains errors")
            return body

        raise IChefAPIError("iCHEF request failed after retries") from last_error


def _retry_delay(attempt: int) -> float:
    return min(2 ** (attempt - 1), 4)
