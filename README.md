# Lefiya Schedule Bot

從 iCHEF 讀取蕾菲亞小精靈班表，透過 LINE Official Account 廣播給全部好友；容器預設同時執行自動排程與 LINE webhook。

## Docker 部署

需求：Docker、HTTPS reverse proxy，以及 LINE Official Account 設定。

建立 `/etc/lefiya-schedule-bot.env`，不要提交到 Git：

```dotenv
LINE_CHANNEL_ACCESS_TOKEN=your-channel-access-token
LINE_CHANNEL_SECRET=your-channel-secret
ICHEF_PUBLIC_ID=WqxdHUPa
APP_TIMEZONE=Asia/Taipei
LOG_LEVEL=INFO
JOB_LOCK_PATH=/tmp/lefiya-schedule-bot/job.lock
```

| 變數 | 必要 | 預設值 | 用途 |
|---|---:|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | 是 | — | LINE 廣播授權 |
| `LINE_CHANNEL_SECRET` | 是 | — | webhook 簽章驗證 |
| `ICHEF_PUBLIC_ID` | 否 | `WqxdHUPa` | iCHEF 商店 ID |
| `APP_TIMEZONE` | 否 | `Asia/Taipei` | 排程與日期時區 |
| `LOG_LEVEL` | 否 | `INFO` | log 等級 |
| `JOB_LOCK_PATH` | 否 | `/tmp/lefiya-schedule-bot/job.lock` | 廣播互斥鎖 |

建立並啟動服務：

```bash
docker build --tag lefiya-schedule-bot:latest .
docker run --detach --name lefiya-schedule-bot --restart unless-stopped --env-file /etc/lefiya-schedule-bot.env --publish 127.0.0.1:8080:8080 lefiya-schedule-bot:latest
```

Reverse proxy 將 `https://你的網域/callback` 轉到容器；健康檢查使用 `GET https://你的網域/health`，預期 HTTP `204`。在 LINE Developers Console 設定 Webhook
URL 並啟用 webhook；8080 不要直接公開到網際網路。

## 自動排程

| 時間（`APP_TIMEZONE`） | 行為 |
|---|---|
| 13:35 | scheduler 啟動工作 |
| 13:40 前 | 等待，不抓取班表 |
| 13:40–15:00 | 每五分鐘抓取，成功後廣播一次 |
| 15:00 起 | 不開始新的 HTTP 請求，等待隔日 |

自動模式每日使用固定 retry key；廣播成功或同一 key 已被 LINE 接受，都視為完成。

## 手動補抓與預覽

同一容器內執行手動補抓：

```bash
docker exec lefiya-schedule-bot python -m lefiya_schedule_bot --manual
docker exec lefiya-schedule-bot python -m lefiya_schedule_bot --manual --date 2026-09-02
```

`--date` 必須是 `YYYY-MM-DD`，預設為 `APP_TIMEZONE` 的今天，不允許未來日期。手動模式
立即執行一次，不受 13:40 或 15:00 限制；未指定 retry key 時每次都會產生 UUID，正式
重送給全部好友。

LINE timeout／5xx 而結果不明時，於 24 小時內使用相同 retry key：

```bash
docker exec lefiya-schedule-bot python -m lefiya_schedule_bot \
  --manual --date 2026-09-02 \
  --retry-key 123e4567-e89b-12d3-a456-426614174000
```

相同 key 回應 `409` 視為已處理。只查看訊息、不呼叫 LINE：

```bash
docker exec lefiya-schedule-bot python -m lefiya_schedule_bot --dry-run
docker exec lefiya-schedule-bot python -m lefiya_schedule_bot --dry-run --date 2026-09-02
```

預覽不需要 access token、不取得廣播鎖，不能與 `--manual` 或 `--retry-key` 合用；stdout 是
訊息，stderr 是 log。全好友廣播會消耗 LINE 每月訊息額度。

iCHEF 可能回傳多個日期分類。目標日期是第一個日期時，會依原 Telegram 範例合併所有
有日期分類；其他過去日期只使用該日期分類，可能記錄 `mixed_schedule_dates`。

## 鎖與獨立工作容器

`docker exec` 與 scheduler 共用容器鎖；第二個同時執行的廣播工作會記錄
`job_already_running` 並略過。若使用 one-shot container，常駐與 one-shot 必須掛載同一
個主機目錄：

```bash
mkdir -p /srv/lefiya-lock
docker run --rm --mount type=bind,src=/srv/lefiya-lock,dst=/tmp/lefiya-schedule-bot \
  --env-file /etc/lefiya-schedule-bot.env lefiya-schedule-bot:latest python -m lefiya_schedule_bot --manual
```

部署常駐容器時也使用相同 `--mount`，並確認容器使用者可寫入目錄。不同主機的鎖檔不具
備分散式鎖功能。

## Log 與故障排查

Log 是輸出到 stderr 的結構化 JSON：

```bash
docker logs --follow lefiya-schedule-bot
docker logs lefiya-schedule-bot 2>&1 | jq -R 'fromjson? | select(.event == "broadcast_sent" or .event == "already_sent" or .event == "manual_failed" or .event == "scheduler_job_failed")'
```

優先查看：`broadcast_sent`／`already_sent`（LINE 已接受）、`manual_*`（手動結果與 retry
key）、`schedule_fetch_completed`（日期與人數）、`deadline_exceeded`／`scheduler_job_failed`
（自動失敗）、`job_already_running`（鎖忙碌）及 `mixed_schedule_dates`（跨日期資料）。
`/health` 只代表 webhook 存活，不代表今日班表已廣播。`docker exec` 的手動 log 只顯示在
該次 terminal；要保留記錄，請保存 one-shot Job log。

## 退出碼

| Code | 意義 |
|---:|---|
| `0` | 廣播成功、LINE 已接受、預覽成功或鎖忙碌而略過 |
| `1` | 班表／上游錯誤、LINE 發送失敗或自動排程逾時 |
| `2` | 環境設定或 CLI 參數錯誤 |
