from __future__ import annotations

from datetime import date

import pytest
from conftest import category, menu_response

from lefiya_schedule_bot.models import (
    MenuDataError,
    Schedule,
    parse_category_date,
    parse_daily_schedules,
)


def test_schedule_mapping_and_emoji() -> None:
    assert Schedule.from_category_name("20260901 午安") is Schedule.DAY
    assert Schedule.from_category_name("20260901 午晚安") is Schedule.ALL
    assert Schedule.from_category_name("20260901 晚安") is Schedule.NIGHT
    assert Schedule.from_category_name("20260901 未分類") is Schedule.NIGHT
    assert Schedule.DAY.emoji_for(True) == "☀️"
    assert Schedule.DAY.emoji_for(False) == "🌞"


def test_parse_category_date_is_strict_and_validates_calendar() -> None:
    assert parse_category_date(" 20260901 午安") == date(2026, 9, 1)
    assert parse_category_date("活動 20260901") is None
    assert parse_category_date("202609011 午安") is None
    with pytest.raises(MenuDataError, match="invalid category date"):
        parse_category_date("20260230 午安")


def test_parse_multiple_dates_photo_options_and_sorting() -> None:
    data = menu_response(
        [
            category("20260902 晚安", "明日夜班"),
            category("20260901 晚安", "夜班"),
            category("20260901 午安", "午班", "可手機拍", "豪華拍立得"),
            category("沒有日期的商品", "略過"),
        ]
    )

    schedules = parse_daily_schedules(data)

    today = schedules[date(2026, 9, 1)]
    assert [fairy.name for fairy in today.fairies] == ["午班", "夜班"]
    assert today.fairies[0].has_phone_photo is True
    assert today.fairies[0].has_deluxe_photo is True
    assert schedules[date(2026, 9, 2)].fairies[0].name == "明日夜班"


def test_parse_target_first_date_matches_reference_aggregation() -> None:
    data = menu_response(
        [
            category("20260904 午安", "宮"),
            category("20260903 午晚安", "黎貝洛"),
            category("20260904 午晚安", "露易絲"),
        ]
    )

    schedules = parse_daily_schedules(data, target_date=date(2026, 9, 4))

    assert list(schedules) == [date(2026, 9, 4)]
    assert [fairy.name for fairy in schedules[date(2026, 9, 4)].fairies] == [
        "宮",
        "黎貝洛",
        "露易絲",
    ]


def test_parse_target_non_first_date_stays_date_filtered() -> None:
    data = menu_response(
        [
            category("20260904 午安", "宮"),
            category("20260903 午晚安", "黎貝洛"),
        ]
    )

    schedules = parse_daily_schedules(data, target_date=date(2026, 9, 3))

    assert [fairy.name for fairy in schedules[date(2026, 9, 3)].fairies] == [
        "黎貝洛"
    ]


def test_parse_empty_dated_category_is_represented() -> None:
    schedules = parse_daily_schedules(menu_response([category("20260901 午安", None)]))

    assert schedules[date(2026, 9, 1)].fairies == ()


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"data": {"restaurant": {"menu": {"categoriesSnapshot": {}}}}},
        menu_response([{"menuItemSnapshot": []}]),
        menu_response([category("沒有日期", "芙蘭")]),
        menu_response([{"name": "20260901 午安", "menuItemSnapshot": [{}]}]),
    ],
)
def test_parse_rejects_missing_or_malformed_fields(data: dict) -> None:
    with pytest.raises(MenuDataError):
        parse_daily_schedules(data)
