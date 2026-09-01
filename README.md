# Lefiya Schedule Bot

每天從 iCHEF 讀取蕾菲亞小精靈的今日班表，並透過 LINE Official Account
Messaging API 廣播給所有好友。容器採單次執行模式：由部署主機每天 13:40
（Asia/Taipei）啟動，每五分鐘檢查一次，取得今日班表後送出並結束；15:00
仍無資料則以非零狀態結束。

同一映像也提供獨立的長駐 webhook receiver，接收 LINE 好友訊息、加好友
（`follow`）及其他 webhook 事件。receiver 會在解析 JSON 前，以
`LINE_CHANNEL_SECRET` 驗證原始 request body 的 `x-line-signature`。

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

本機啟動 webhook receiver：

```bash
export LINE_CHANNEL_SECRET="test-channel-secret"
PYTHONPATH=src gunicorn --bind 127.0.0.1:8080 \
  'lefiya_schedule_bot.webhook:create_app()'
```

webhook endpoint 是 `POST /webhooks/line`，健康檢查是 `GET /health`。預設事件
handler 會安全地記錄事件種類、訊息種類與 webhook event ID，不會記錄訊息內容
或 LINE user ID。自動回覆或資料保存應透過自訂 event handler 另行實作。

環境變數：

| 名稱 | 使用程序 | 必要 | 預設值 | 說明 |
|---|---|---:|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | 廣播 | 是 | — | 呼叫 LINE Messaging API 的 channel access token |
| `LINE_CHANNEL_SECRET` | webhook | 是 | — | 驗證 LINE webhook 簽章的 channel secret |
| `ICHEF_PUBLIC_ID` | 廣播 | 否 | `WqxdHUPa` | iCHEF 商店 public ID |
| `APP_TIMEZONE` | 廣播 | 否 | `Asia/Taipei` | IANA timezone |
| `LOG_LEVEL` | 兩者 | 否 | `INFO` | Python logging level |

## 部署

建立映像：

```bash
docker build --tag lefiya-schedule-bot:latest .
```

在部署主機建立兩個不納入版本控制的環境檔。分開保存可避免兩個程序取得不需要
的 LINE 機密。

`/etc/lefiya-schedule-broadcast.env`：

```dotenv
LINE_CHANNEL_ACCESS_TOKEN=your-long-lived-channel-access-token
ICHEF_PUBLIC_ID=WqxdHUPa
APP_TIMEZONE=Asia/Taipei
LOG_LEVEL=INFO
```

`/etc/lefiya-schedule-webhook.env`：

```dotenv
LINE_CHANNEL_SECRET=your-channel-secret
LOG_LEVEL=INFO
```

限制環境檔只能由部署帳號讀取：

```bash
chmod 600 /etc/lefiya-schedule-broadcast.env
chmod 600 /etc/lefiya-schedule-webhook.env
```

於台北時間 13:40–15:00 間手動執行：

```bash
docker run --rm \
  --name lefiya-schedule-bot \
  --env-file /etc/lefiya-schedule-broadcast.env \
  lefiya-schedule-bot:latest
```

正式環境由主機 cron 每天 13:40 啟動。以下範例假設主機時區已設為
`Asia/Taipei`：

```cron
40 13 * * * /usr/bin/docker run --rm --name lefiya-schedule-bot --env-file /etc/lefiya-schedule-broadcast.env lefiya-schedule-bot:latest >> /var/log/lefiya-schedule-bot.log 2>&1
```

若主機使用其他時區，請依主機 cron 的時區設定換算執行時間。不要替此容器設定
`--restart=always`：成功廣播或 15:00 逾時後，容器都應保持停止，等待隔日 cron
重新啟動。

webhook receiver 則需保持長駐：

```bash
docker run --detach \
  --name lefiya-schedule-webhook \
  --restart unless-stopped \
  --env-file /etc/lefiya-schedule-webhook.env \
  --publish 127.0.0.1:8080:8080 \
  lefiya-schedule-bot:latest \
  gunicorn --workers 2 --bind 0.0.0.0:8080 \
  'lefiya_schedule_bot.webhook:create_app()'
```

在 webhook container 前配置具有有效公開 TLS 憑證的 reverse proxy，將
`https://你的網域/webhooks/line` 轉送到 `127.0.0.1:8080`。接著在 LINE
Developers Console 將該 HTTPS URL 設為 Webhook URL、按 Verify，並啟用
webhook。不要直接把 8080 port 公開到網際網路。

LINE 可能重新投遞 webhook。預設 handler 只有記錄事件；未來若加入回覆、寫入
資料庫等副作用，應以 `webhookEventId` 實作持久化去重。

程式退出碼：

| 退出碼 | 意義 |
|---:|---|
| `0` | 廣播成功，或相同 retry key 已由 LINE 接受 |
| `1` | 15:00 無今日班表、上游資料錯誤或 LINE 發送失敗 |
| `2` | 環境設定錯誤 |

LINE 全好友廣播會依可接收好友人數計入每月訊息額度。啟用排程前，請先確認
Official Account 方案足以負擔「好友數 × 當月發送天數」。
