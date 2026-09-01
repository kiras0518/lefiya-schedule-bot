# Lefiya Schedule Bot

每天從 iCHEF 讀取蕾菲亞小精靈的今日班表，並透過 LINE Official Account
Messaging API 廣播給所有好友。容器採單次執行模式：由部署主機每天 13:40
（Asia/Taipei）啟動，每五分鐘檢查一次，取得今日班表後送出並結束；15:00
仍無資料則以非零狀態結束。

## 本機開發

需要 Python 3.12：

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check .
```

本機執行會真的向全部 LINE 好友廣播，請只使用測試用 Official Account：

```bash
export LINE_CHANNEL_ACCESS_TOKEN="test-channel-access-token"
export ICHEF_PUBLIC_ID="WqxdHUPa"
export APP_TIMEZONE="Asia/Taipei"
export LOG_LEVEL="INFO"
PYTHONPATH=src python -m lefiya_schedule_bot
```

環境變數：

| 名稱 | 必要 | 預設值 | 說明 |
|---|---:|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | 是 | — | LINE Messaging API long-lived channel access token |
| `ICHEF_PUBLIC_ID` | 否 | `WqxdHUPa` | iCHEF 商店 public ID |
| `APP_TIMEZONE` | 否 | `Asia/Taipei` | IANA timezone |
| `LOG_LEVEL` | 否 | `INFO` | Python logging level |

## 部署

建立映像：

```bash
docker build --tag lefiya-schedule-bot:latest .
```

在部署主機建立不納入版本控制的環境檔，例如
`/etc/lefiya-schedule-bot.env`：

```dotenv
LINE_CHANNEL_ACCESS_TOKEN=your-long-lived-channel-access-token
ICHEF_PUBLIC_ID=WqxdHUPa
APP_TIMEZONE=Asia/Taipei
LOG_LEVEL=INFO
```

限制環境檔只能由部署帳號讀取：

```bash
chmod 600 /etc/lefiya-schedule-bot.env
```

於台北時間 13:40–15:00 間手動執行：

```bash
docker run --rm \
  --name lefiya-schedule-bot \
  --env-file /etc/lefiya-schedule-bot.env \
  lefiya-schedule-bot:latest
```

正式環境由主機 cron 每天 13:40 啟動。以下範例假設主機時區已設為
`Asia/Taipei`：

```cron
40 13 * * * /usr/bin/docker run --rm --name lefiya-schedule-bot --env-file /etc/lefiya-schedule-bot.env lefiya-schedule-bot:latest >> /var/log/lefiya-schedule-bot.log 2>&1
```

若主機使用其他時區，請依主機 cron 的時區設定換算執行時間。不要替此容器設定
`--restart=always`：成功廣播或 15:00 逾時後，容器都應保持停止，等待隔日 cron
重新啟動。

程式退出碼：

| 退出碼 | 意義 |
|---:|---|
| `0` | 廣播成功，或相同 retry key 已由 LINE 接受 |
| `1` | 15:00 無今日班表、上游資料錯誤或 LINE 發送失敗 |
| `2` | 環境設定錯誤 |

LINE 全好友廣播會依可接收好友人數計入每月訊息額度。啟用排程前，請先確認
Official Account 方案足以負擔「好友數 × 當月發送天數」。
