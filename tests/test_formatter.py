from __future__ import annotations

from datetime import date

from lefiya_schedule_bot.formatter import format_schedule_message
from lefiya_schedule_bot.models import DailySchedule, Fairy, Schedule


def test_format_schedule_message_preserves_existing_content() -> None:
    schedule = DailySchedule(
        date(2026, 9, 1),
        (
            Fairy("芙蘭", Schedule.DAY, True, True),
            Fairy("蕾菲亞", Schedule.NIGHT, False, False),
        ),
    )

    assert format_schedule_message(schedule) == (
        "20260901 出勤的小精靈有：\n\n"
        "芙蘭 ☀️✨\n"
        "蕾菲亞 🌛\n\n"
        "今日營運時間：\n"
        "☀️：14:00 ~ 18:00\n"
        "🌍：14:00 ~ 22:00\n"
        "🌙：18:00 ~ 22:00\n"
        "實際班表以現場為準\n\n"
        "線上點拍連結：\n"
        "https://order.lefiya.com\n\n"
        "✨ 代表可點豪華拍"
    )
