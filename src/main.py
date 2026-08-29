#!/usr/bin/python3

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Dict, Any

import requests


class Schedule(Enum):
    DAY = ("午安", "☀️", 1)
    ALL = ("午晚安", "🌍", 2)
    NIGHT = ("晚安", "🌙", 3)

    def __init__(self, keyword: str, emoji: str, order: int):
        self.keyword = keyword
        self.emoji = emoji
        self.order = order

    @classmethod
    def from_name(cls, name: str) -> "Schedule":
        for schedule in cls:
            if schedule.keyword in name:
                return schedule
        return cls.NIGHT

    def emoji_for(self, has_phone_photo: bool) -> str:
        if has_phone_photo:
            return self.emoji

        return {
            Schedule.DAY: "🌞",
            Schedule.ALL: "🌎",
            Schedule.NIGHT: "🌛",
        }[self]


@dataclass
class Fairy:
    name: str
    schedule: Schedule
    has_phone_photo: bool
    has_deluxe_photo: bool


@dataclass
class BotConfig:
    token: str
    channel_id: str

    @classmethod
    def from_file(cls, path: str = "config.json") -> "BotConfig":
        with open(path) as f:
            data = json.load(f)
            return cls(data["token"], data["channel_id"])


class TelegramBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.api_url = f"https://api.telegram.org/bot{config.token}/"

    def send_message(self, text: str) -> bool:
        payload = {
            'chat_id': self.config.channel_id,
            'text': text,
            'link_preview_options': {'is_disabled': True}
        }
        try:
            response = requests.post(f'{self.api_url}sendMessage', json=payload)
            return response.status_code == 200
        except Exception as e:
            print(f"Error sending message: {e}")
            return False


class IChefAPI:
    BASE_URL = "https://shop.ichefpos.com/api/graphql/online_restaurant"
    PUBLIC_ID = "WqxdHUPa"

    @staticmethod
    def _make_request(
        operation: str, query: str, variables: Dict[str, Any]
    ) -> Dict[str, Any]:
        url = f"{IChefAPI.BASE_URL}?op={operation}"
        headers = {"Content-Type": "application/json", "cache-control": "no-cache"}
        payload = {"operationName": operation, "variables": variables, "query": query}

        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"API request failed: {e}")
            return {}

    @classmethod
    def fetch_menu_hours(cls) -> List[str]:
        uuids = []
        query = """query menuHoursSnapshotQuery($publicId: String!, $platformType: PlatformTypes!) {
  restaurant(publicId: $publicId) {
    onlineOrderingMenu(platformType: $platformType) {
      menuHoursSnapshot {
        categorySnapshotUuids
      }
    }
  }
}"""
        variables = {"publicId": cls.PUBLIC_ID, "platformType": "ICHEF"}

        result = cls._make_request("menuHoursSnapshotQuery", query, variables)
        try:
            snapshots = result["data"]["restaurant"]["onlineOrderingMenu"][
                "menuHoursSnapshot"
            ]
            for snapshot in snapshots:
                uuids.extend(snapshot["categorySnapshotUuids"])
            return uuids
        except (KeyError, IndexError):
            return uuids

    @classmethod
    def fetch_menu_items(cls, uuids: List[str]) -> Dict[str, Any]:
        query = """query restaurantMenuItemCategoriesQuery($publicId: String, $categoriesSnapshotUuids: [UUID!]!) {
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
        variables = {"publicId": cls.PUBLIC_ID, "categoriesSnapshotUuids": uuids}

        return cls._make_request("restaurantMenuItemCategoriesQuery", query, variables)


class ScheduleBot:
    OPENING_HOURS = "\n今日營運時間：\n☀️：14:00 ~ 18:00\n🌍：14:00 ~ 22:00\n🌙：18:00 ~ 22:00\n"
    PHONE_PHOTO = "手機拍"
    DELUXE_PHOTO = "豪華拍"

    def __init__(self, config: BotConfig):
        self.bot = TelegramBot(config)
        self.api = IChefAPI()
        self.record_file = Path("record.json")

    def get_fairies(self) -> tuple[List[Fairy], str]:
        uuids = self.api.fetch_menu_hours()
        if not uuids:
            return [], ""

        items_data = self.api.fetch_menu_items(uuids)
        return self._parse_fairies(items_data)

    @classmethod
    def _get_photo_flags(cls, item: Dict[str, Any]) -> tuple[bool, bool]:
        has_phone_photo = False
        has_deluxe_photo = False

        for group in item.get("modifierGroupSnapshot", []):
            if not group.get("modifierOptionSnapshot"):
                continue

            name = group.get("name", "")
            if cls.PHONE_PHOTO in name:
                has_phone_photo = True
            if cls.DELUXE_PHOTO in name:
                has_deluxe_photo = True
            if has_phone_photo and has_deluxe_photo:
                break

        return has_phone_photo, has_deluxe_photo

    def _parse_fairies(self, data: Dict[str, Any]) -> tuple[List[Fairy], str]:
        fairies = []
        date = ""

        try:
            categories = data["data"]["restaurant"]["menu"]["categoriesSnapshot"]
            for category in categories:
                name = category["name"]
                if not date and len(name) >= 8:
                    date = name.strip()[:8]

                schedule = Schedule.from_name(name)
                for item in category.get("menuItemSnapshot", []):
                    has_phone_photo, has_deluxe_photo = self._get_photo_flags(item)
                    fairies.append(
                        Fairy(
                            item["name"],
                            schedule,
                            has_phone_photo,
                            has_deluxe_photo,
                        )
                    )

            fairies.sort(key=lambda f: f.schedule.order)
        except (KeyError, TypeError) as e:
            print(f"Error parsing data: {e}")

        return fairies, date

    def format_message(self, fairies: List[Fairy], date: str) -> str:
        message = f"{date} 出勤的小精靈有：\n\n"
        message += "\n".join(
            f"{fairy.name} "
            f"{fairy.schedule.emoji_for(fairy.has_phone_photo)}"
            f"{'✨' if fairy.has_deluxe_photo else ''}"
            for fairy in fairies
        )

        message += self.OPENING_HOURS
        message += "實際班表以現場為準\n\n線上點拍連結：\nhttps://order.lefiya.com"
        return message

    def should_send(self) -> bool:
        return self._is_new_day() and self._is_send_time()

    def _is_new_day(self) -> bool:
        if not self.record_file.exists():
            return True

        try:
            with open(self.record_file) as f:
                record = json.load(f)
                last_date = int(record.get("date", 0))
                today = int(datetime.now().strftime("%Y%m%d"))
                return last_date < today
        except (json.JSONDecodeError, ValueError):
            return True

    def _is_send_time(self) -> bool:
        now = datetime.now()
        hour, minute = now.hour, now.minute

        in_time = (hour == 13 and minute > 40) or (hour == 14 and minute < 59)

        if not in_time:
            print(f"Not in send time: {hour:02d}:{minute:02d}")
        return in_time

    def update_record(self, date: str):
        with open(self.record_file, "w") as f:
            json.dump({"date": date}, f)

    def run_once(self):
        fairies, date = self.get_fairies()
        if not fairies or not date:
            print("No data available")
            return

        today = datetime.now().strftime("%Y%m%d")
        if int(date) < int(today):
            print(f"Data not updated yet: {date} < {today}")
            return

        message = self.format_message(fairies, date)
        if self.bot.send_message(message):
            self.update_record(date)

    def run(self):
        while True:
            if self.should_send():
                self.run_once()
            time.sleep(60)


def main():
    try:
        config = BotConfig.from_file()
        bot = ScheduleBot(config)

        if len(sys.argv) == 2 and sys.argv[1] == "force":
            bot.run_once()
        else:
            bot.run()
    except FileNotFoundError:
        print("Error: config.json not found")
    except KeyboardInterrupt:
        print("\nStopped")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
