from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

import pytest

from lefiya_schedule_bot.config import WebhookSettings
from lefiya_schedule_bot.webhook import (
    MAX_WEBHOOK_BODY_BYTES,
    create_app,
    log_line_event,
    verify_line_signature,
)

CHANNEL_SECRET = "channel-secret"


def sign(body: bytes, secret: str = CHANNEL_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_verify_line_signature_matches_official_example() -> None:
    body = b'{"destination":"U8e742f61d673b39c7fff3cecb7536ef0","events":[]}'

    assert verify_line_signature(
        body,
        "GhRKmvmHys4Pi8DxkF4+EayaH0OqtJtaZxgTD9fMDLs=",
        "8c570fa6dd201bb328f1c1eac23a96d8",
    )


@pytest.mark.parametrize("path", ["/callback", "/webhooks/line"])
def test_webhook_dispatches_message_and_follow_events(path: str) -> None:
    received: list[dict[str, Any]] = []
    app = create_app(
        WebhookSettings(CHANNEL_SECRET),
        event_handler=received.append,
    )
    body = json.dumps(
        {
            "destination": "Ubot",
            "events": [
                {
                    "type": "message",
                    "webhookEventId": "event-message",
                    "message": {"id": "message-1", "type": "text", "text": "你好"},
                    "source": {"type": "user", "userId": "Ufriend"},
                },
                {
                    "type": "follow",
                    "webhookEventId": "event-follow",
                    "source": {"type": "user", "userId": "Ufriend"},
                },
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()

    response = app.test_client().post(
        path,
        data=body,
        headers={"x-line-signature": sign(body)},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert [event["type"] for event in received] == ["message", "follow"]
    assert received[0]["message"]["text"] == "你好"


def test_webhook_logs_request_lifecycle_and_correlates_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(WebhookSettings(CHANNEL_SECRET))
    body = json.dumps(
        {
            "destination": "Ubot",
            "events": [{"type": "follow", "webhookEventId": "event-follow"}],
        },
        separators=(",", ":"),
    ).encode()

    with caplog.at_level(logging.INFO):
        response = app.test_client().post(
            "/callback",
            data=body,
            headers={"x-line-signature": sign(body)},
            content_type="application/json",
        )

    assert response.status_code == 200
    records = {
        record.event: record
        for record in caplog.records
        if getattr(record, "event", None)
        in {
            "webhook_request_started",
            "line_webhook_event_received",
            "line_webhook_accepted",
            "webhook_request_completed",
        }
    }
    assert records["webhook_request_started"].request_kind == "line_webhook"
    request_id = records["webhook_request_started"].request_id
    assert records["line_webhook_event_received"].request_id == request_id
    assert records["line_webhook_accepted"].request_id == request_id
    assert records["line_webhook_accepted"].event_count == 1
    assert records["webhook_request_completed"].request_id == request_id
    assert records["webhook_request_completed"].status_code == 200
    assert records["webhook_request_completed"].duration_ms >= 0


def test_webhook_accepts_line_console_verification_request() -> None:
    app = create_app(WebhookSettings(CHANNEL_SECRET))
    body = b'{"destination":"Ubot","events":[]}'

    response = app.test_client().post(
        "/callback",
        data=body,
        headers={"x-line-signature": sign(body)},
        content_type="application/json",
    )

    assert response.status_code == 200


def test_webhook_rejects_missing_or_tampered_signature() -> None:
    received: list[dict[str, Any]] = []
    app = create_app(
        WebhookSettings(CHANNEL_SECRET),
        event_handler=received.append,
    )
    body = b'{"events":[]}'
    client = app.test_client()

    missing = client.post("/webhooks/line", data=body)
    tampered = client.post(
        "/webhooks/line",
        data=body + b" ",
        headers={"x-line-signature": sign(body)},
    )

    assert missing.status_code == 401
    assert tampered.status_code == 401
    assert received == []


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"events":"not-a-list"}',
        b'{"events":[1]}',
    ],
)
def test_webhook_rejects_signed_invalid_payload(body: bytes) -> None:
    app = create_app(WebhookSettings(CHANNEL_SECRET))

    response = app.test_client().post(
        "/webhooks/line",
        data=body,
        headers={"x-line-signature": sign(body)},
    )

    assert response.status_code == 400


def test_webhook_rejects_body_over_size_limit() -> None:
    app = create_app(WebhookSettings(CHANNEL_SECRET))
    body = b"x" * (MAX_WEBHOOK_BODY_BYTES + 1)

    response = app.test_client().post(
        "/webhooks/line",
        data=body,
        headers={"x-line-signature": sign(body)},
    )

    assert response.status_code == 413


def test_webhook_returns_error_so_line_can_retry_handler_failure() -> None:
    def fail(_event: dict[str, Any]) -> None:
        raise RuntimeError("temporary failure")

    app = create_app(WebhookSettings(CHANNEL_SECRET), event_handler=fail)
    body = b'{"events":[{"type":"follow"}]}'

    response = app.test_client().post(
        "/webhooks/line",
        data=body,
        headers={"x-line-signature": sign(body)},
    )

    assert response.status_code == 500


def test_health_endpoint_does_not_require_line_signature() -> None:
    app = create_app(WebhookSettings(CHANNEL_SECRET))

    response = app.test_client().get("/health")

    assert response.status_code == 204


def test_default_event_log_excludes_message_content_and_user_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = {
        "type": "message",
        "webhookEventId": "event-1",
        "message": {"type": "text", "text": "private message"},
        "source": {"type": "user", "userId": "private-user-id"},
        "deliveryContext": {"isRedelivery": True},
    }

    with caplog.at_level(logging.INFO):
        log_line_event(event)

    record = caplog.records[-1]
    assert record.event_type == "message"  # type: ignore[attr-defined]
    assert record.message_type == "text"  # type: ignore[attr-defined]
    assert record.source_type == "user"  # type: ignore[attr-defined]
    assert record.is_redelivery is True  # type: ignore[attr-defined]
    assert "private message" not in record.getMessage()
    assert "private-user-id" not in record.getMessage()
