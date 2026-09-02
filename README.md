# Lefiya Schedule Bot

每天從 iCHEF 讀取蕾菲亞小精靈的今日班表，並透過 LINE Official Account
Messaging API 廣播給所有好友。廣播程序採單次執行模式：由部署主機每天 13:40
（Asia/Taipei）啟動，每五分鐘檢查一次，取得今日班表後送出並結束；15:00
仍無資料則以非零狀態結束。

Docker 映像預設啟動長駐 webhook receiver，接收 LINE 好友訊息、加好友
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

手動補抓會立即執行一次，不受 13:40 開始時間與 15:00 截止時間限制：

```bash
PYTHONPATH=src python -m lefiya_schedule_bot --manual
PYTHONPATH=src python -m lefiya_schedule_bot --manual --date 2026-09-02
```

`--date` 使用 `YYYY-MM-DD`，預設為 `APP_TIMEZONE` 的今天，只能指定今天或過去
日期。手動模式若未指定 `--retry-key`，每次執行都會產生新的 key 並重新廣播。若
LINE 回應逾時或 5xx 而結果不明，請從 structured log 取出該次的 retry key，於
24 小時內使用相同 key 重試：

```bash
PYTHONPATH=src python -m lefiya_schedule_bot \
  --manual --date 2026-09-02 \
  --retry-key 123e4567-e89b-12d3-a456-426614174000
```

手動模式查不到目標日期或班表為空時會以非零狀態結束，且不會發送訊息。`--date`
與 `--retry-key` 必須和 `--manual` 一起使用。

本機啟動 webhook receiver：

```bash
export LINE_CHANNEL_SECRET="test-channel-secret"
PYTHONPATH=src gunicorn --bind "127.0.0.1:${PORT:-8080}" \
  'lefiya_schedule_bot.webhook:create_app()'
```

webhook endpoint 是 `POST /callback`；`POST /webhooks/line` 是相同 handler 的
別名。健康檢查是 `GET /health`。預設事件 handler 會安全地記錄事件種類、訊息
種類與 webhook event ID，不會記錄訊息內容或 LINE user ID。自動回覆或資料
保存應透過自訂 event handler 另行實作。

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

自動模式由主機 cron 每天 13:40 啟動。正式環境的自動與手動廣播都必須使用同一個
主機層級鎖，避免兩個 Docker container 同時執行。以下範例使用 `flock`；請確認
執行帳號可以建立或寫入 `/var/lock/lefiya-schedule-bot-broadcast.lock`。

手動補抓可以在 15:00 後執行：

```bash
flock -n /var/lock/lefiya-schedule-bot-broadcast.lock \
docker run --rm --name lefiya-schedule-bot-manual \
  --env-file /etc/lefiya-schedule-broadcast.env \
  lefiya-schedule-bot:latest \
  python -m lefiya_schedule_bot --manual
```

指定日期或復原不確定的 LINE 請求：

```bash
flock -n /var/lock/lefiya-schedule-bot-broadcast.lock \
docker run --rm --name lefiya-schedule-bot-manual \
  --env-file /etc/lefiya-schedule-broadcast.env \
  lefiya-schedule-bot:latest \
  python -m lefiya_schedule_bot --manual \
  --date 2026-09-02 \
  --retry-key 123e4567-e89b-12d3-a456-426614174000
```

以下 cron 範例假設主機時區已設為
`Asia/Taipei`：

```cron
40 13 * * * /usr/bin/flock -n /var/lock/lefiya-schedule-bot-broadcast.lock /usr/bin/docker run --rm --name lefiya-schedule-bot --env-file /etc/lefiya-schedule-broadcast.env lefiya-schedule-bot:latest python -m lefiya_schedule_bot >> /var/log/lefiya-schedule-bot.log 2>&1
```

若主機使用其他時區，請依主機 cron 的時區設定換算執行時間。若 `flock` 取得失敗，
該次工作會直接結束且不會發送。不要替此容器設定
`--restart=always`：成功廣播或 15:00 逾時後，容器都應保持停止，等待隔日 cron
重新啟動。

webhook receiver 則需保持長駐：

```bash
docker run --detach \
  --name lefiya-schedule-webhook \
  --restart unless-stopped \
  --env-file /etc/lefiya-schedule-webhook.env \
  --publish 127.0.0.1:8080:8080 \
  lefiya-schedule-bot:latest
```

在 webhook container 前配置具有有效公開 TLS 憑證的 reverse proxy，將
`https://你的網域/callback` 轉送到 `127.0.0.1:8080`。接著在 LINE
Developers Console 將該 HTTPS URL 設為 Webhook URL、按 Verify，並啟用
webhook。不要直接把 8080 port 公開到網際網路。

### Zeabur

從 Git repository 部署時，Zeabur 會使用根目錄的 Dockerfile。請在服務的
Variables 設定 `LINE_CHANNEL_SECRET`；Docker 預設命令會啟動 Gunicorn，並監聽
Zeabur 注入的 `PORT`（未提供時使用 8080）。部署後設定：

```text
Webhook URL: https://lefiya-schedule-bot.zeabur.app/callback
Health URL:  https://lefiya-schedule-bot.zeabur.app/health
```

`GET /health` 應回 `204`，LINE Developers Console 的 Verify 應回成功。每日
broadcaster 應使用另一個排程服務，執行 `python -m lefiya_schedule_bot`；需要
補抓時執行 `python -m lefiya_schedule_bot --manual`。排程器必須設定同一時間只能有
一個 broadcaster job 執行；不要在 webhook service 內取代預設啟動命令。

LINE 可能重新投遞 webhook。預設 handler 只有記錄事件；未來若加入回覆、寫入
資料庫等副作用，應以 `webhookEventId` 實作持久化去重。

程式退出碼：

| 退出碼 | 意義 |
|---:|---|
| `0` | 廣播成功，或相同 retry key 已由 LINE 接受 |
| `1` | 自動模式逾時、手動模式無目標班表、上游資料錯誤、互斥鎖忙碌或 LINE 發送失敗 |
| `2` | 環境設定錯誤或 CLI 參數錯誤 |

LINE 全好友廣播會依可接收好友人數計入每月訊息額度。啟用排程前，請先確認
Official Account 方案足以負擔「好友數 × 當月發送天數」。
