from __future__ import annotations

from .models import DailySchedule

OPENING_HOURS = (
    "\n\n今日營運時間：\n☀️：14:00 ~ 18:00\n🌍：14:00 ~ 22:00\n🌙：18:00 ~ 22:00\n"
)


def format_schedule_message(schedule: DailySchedule) -> str:
    date_text = schedule.service_date.strftime("%Y%m%d")
    fairy_lines = "\n".join(
        f"{fairy.name} "
        f"{fairy.schedule.emoji_for(fairy.has_phone_photo)}"
        f"{'✨' if fairy.has_deluxe_photo else ''}"
        for fairy in schedule.fairies
    )
    return (
        f"{date_text} 出勤的小精靈有：\n\n"
        f"{fairy_lines}"
        f"{OPENING_HOURS}"
        "實際班表以現場為準\n\n"
        "線上點拍連結：\n"
        "https://order.lefiya.com\n\n"
        "✨ 代表可點豪華拍"
    )
