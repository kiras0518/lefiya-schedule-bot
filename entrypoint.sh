#!/bin/sh
set -eu

# 05:35 UTC = 13:35 Asia/Taipei (Taiwan has no daylight saving time).
# Start five minutes before START_TIME so the job can enter its waiting window.
TARGET_SECS=$((5 * 3600 + 35 * 60))

log_event() {
    component=$1
    severity=$2
    event=$3
    message=$4
    wait_seconds=${5:-}
    exit_code=${6:-}
    if [ -n "$wait_seconds" ]; then
        printf '{"timestamp":"%s","severity":"%s","event":"%s","message":"%s","component":"%s","wait_seconds":%s}\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$severity" "$event" "$message" "$component" "$wait_seconds"
    elif [ -n "$exit_code" ]; then
        printf '{"timestamp":"%s","severity":"%s","event":"%s","message":"%s","component":"%s","exit_code":%s}\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$severity" "$event" "$message" "$component" "$exit_code"
    else
        printf '{"timestamp":"%s","severity":"%s","event":"%s","message":"%s","component":"%s"}\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$severity" "$event" "$message" "$component"
    fi
}

scheduler_loop() {
    log_event "scheduler" "INFO" "scheduler_started" "scheduler_started"
    while true; do
        secs_today=$(( $(date -u +%s) % 86400 ))
        wait=$((TARGET_SECS - secs_today))
        if [ "$wait" -lt 0 ]; then
            wait=$((wait + 86400))
        fi
        log_event "scheduler" "INFO" "scheduler_waiting" "scheduler_waiting" "$wait"
        sleep "$wait"
        log_event "scheduler" "INFO" "scheduler_job_started" "scheduler_job_started"
        if python -m lefiya_schedule_bot; then
            log_event "scheduler" "INFO" "scheduler_job_completed" "scheduler_job_completed"
        else
            status=$?
            log_event "scheduler" "ERROR" "scheduler_job_failed" "scheduler_job_failed" "" "$status"
            log_event "scheduler" "INFO" "scheduler_job_next_run" "scheduler_job_next_run"
        fi
    done
}

log_event "entrypoint" "INFO" "container_starting" "container_starting"
scheduler_loop &
log_event "entrypoint" "INFO" "webhook_starting" "webhook_starting"

exec gunicorn \
    --workers 2 \
    --bind "0.0.0.0:${PORT:-8080}" \
    'lefiya_schedule_bot.webhook:create_app()'
