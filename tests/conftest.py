from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class StubResponse:
    def __init__(
        self,
        status_code: int,
        body: Any = None,
        headers: dict[str, str] | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = {} if body is None else body
        self.headers = headers or {}
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._body


class StubSession:
    def __init__(self, outcomes: Iterable[Any]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append({"url": url, **kwargs})
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def menu_hours_response(*uuids: str) -> dict[str, Any]:
    return {
        "data": {
            "restaurant": {
                "onlineOrderingMenu": {
                    "menuHoursSnapshot": [{"categorySnapshotUuids": list(uuids)}]
                }
            }
        }
    }


def menu_response(categories: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "data": {
            "restaurant": {
                "menu": {"categoriesSnapshot": categories},
            }
        }
    }


def category(
    name: str,
    fairy_name: str | None = "芙蘭",
    *options: str,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if fairy_name is not None:
        items.append(
            {
                "name": fairy_name,
                "modifierGroupSnapshot": [
                    {
                        "name": "拍照",
                        "modifierOptionSnapshot": [
                            {"name": option} for option in options
                        ],
                    }
                ],
            }
        )
    return {"name": name, "menuItemSnapshot": items}
