from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


class MenuDataError(ValueError):
    """Raised when iCHEF returns an unexpected menu shape."""


class Schedule(Enum):
    DAY = ("午安", "☀️", "🌞", 1)
    ALL = ("午晚安", "🌍", "🌎", 2)
    NIGHT = ("晚安", "🌙", "🌛", 3)

    def __init__(
        self,
        keyword: str,
        phone_photo_emoji: str,
        standard_emoji: str,
        order: int,
    ) -> None:
        self.keyword = keyword
        self.phone_photo_emoji = phone_photo_emoji
        self.standard_emoji = standard_emoji
        self.order = order

    @classmethod
    def from_category_name(cls, name: str) -> Schedule:
        for schedule in cls:
            if schedule.keyword in name:
                return schedule
        return cls.NIGHT

    def emoji_for(self, has_phone_photo: bool) -> str:
        return self.phone_photo_emoji if has_phone_photo else self.standard_emoji


@dataclass(frozen=True)
class Fairy:
    name: str
    schedule: Schedule
    has_phone_photo: bool
    has_deluxe_photo: bool


@dataclass(frozen=True)
class DailySchedule:
    service_date: date
    fairies: tuple[Fairy, ...]


_CATEGORY_DATE_PATTERN = re.compile(r"^\s*(\d{8})(?=\D|$)")


def parse_category_date(name: str) -> date | None:
    match = _CATEGORY_DATE_PATTERN.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError as error:
        raise MenuDataError(f"invalid category date: {match.group(1)}") from error


def parse_daily_schedules(data: Mapping[str, Any]) -> dict[date, DailySchedule]:
    try:
        categories = data["data"]["restaurant"]["menu"]["categoriesSnapshot"]
    except (KeyError, TypeError) as error:
        raise MenuDataError("missing categoriesSnapshot in menu response") from error

    if not isinstance(categories, list):
        raise MenuDataError("categoriesSnapshot must be a list")

    fairies_by_date: dict[date, list[Fairy]] = {}
    found_dated_category = False

    for category in categories:
        if not isinstance(category, Mapping):
            raise MenuDataError("menu category must be an object")

        name = category.get("name")
        if not isinstance(name, str):
            raise MenuDataError("menu category name must be a string")

        service_date = parse_category_date(name)
        if service_date is None:
            continue
        found_dated_category = True

        items = category.get("menuItemSnapshot", [])
        if not isinstance(items, list):
            raise MenuDataError("menuItemSnapshot must be a list")

        schedule = Schedule.from_category_name(name)
        destination = fairies_by_date.setdefault(service_date, [])
        for item in items:
            destination.append(_parse_fairy(item, schedule))

    if categories and not found_dated_category:
        raise MenuDataError("menu response contains no YYYYMMDD category")

    schedules: dict[date, DailySchedule] = {}
    for service_date, fairies in fairies_by_date.items():
        fairies.sort(key=lambda fairy: fairy.schedule.order)
        schedules[service_date] = DailySchedule(service_date, tuple(fairies))
    return schedules


def _parse_fairy(item: Any, schedule: Schedule) -> Fairy:
    if not isinstance(item, Mapping):
        raise MenuDataError("menu item must be an object")

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MenuDataError("menu item name must be a non-empty string")

    has_phone_photo = False
    has_deluxe_photo = False
    groups = item.get("modifierGroupSnapshot", [])
    if not isinstance(groups, list):
        raise MenuDataError("modifierGroupSnapshot must be a list")

    for group in groups:
        if not isinstance(group, Mapping):
            raise MenuDataError("modifier group must be an object")
        options = group.get("modifierOptionSnapshot", [])
        if not isinstance(options, list):
            raise MenuDataError("modifierOptionSnapshot must be a list")
        for option in options:
            if not isinstance(option, Mapping):
                raise MenuDataError("modifier option must be an object")
            option_name = option.get("name", "")
            if not isinstance(option_name, str):
                raise MenuDataError("modifier option name must be a string")
            has_phone_photo = has_phone_photo or "手機拍" in option_name
            has_deluxe_photo = has_deluxe_photo or "豪華拍立得" in option_name

    return Fairy(
        name=name.strip(),
        schedule=schedule,
        has_phone_photo=has_phone_photo,
        has_deluxe_photo=has_deluxe_photo,
    )
