#!/bin/sh
set -eu

# 05:35 UTC = 13:35 Asia/Taipei (Taiwan has no daylight saving time).
# Start five minutes before START_TIME so the job can enter its waiting window.
TARGET_SECS=$((5 * 3600 + 35 * 60))

scheduler_loop() {
    while true; do
        secs_today=$(( $(date -u +%s) % 86400 ))
        wait=$((TARGET_SECS - secs_today))
        if [ "$wait" -lt 0 ]; then
            wait=$((wait + 86400))
        fi
        sleep "$wait"
        python -m lefiya_schedule_bot || \
            echo "scheduler run exited non-zero, will retry tomorrow"
    done
}

scheduler_loop &

exec gunicorn \
    --workers 2 \
    --bind "0.0.0.0:${PORT:-8080}" \
    'lefiya_schedule_bot.webhook:create_app()'
