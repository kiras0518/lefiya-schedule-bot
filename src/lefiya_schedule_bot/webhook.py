from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Callable
from time import perf_counter
from typing import Any

from flask import Flask, Response, g, has_request_context, request

from .config import WebhookSettings
from .logging_config import configure_logging, duration_ms, log_event

EventHandler = Callable[[dict[str, Any]], None]
MAX_WEBHOOK_BODY_BYTES = 1_048_576


class InvalidWebhookSignature(ValueError):
    """Raised when a request cannot be authenticated as coming from LINE."""


class InvalidWebhookPayload(ValueError):
    """Raised when an authenticated webhook body has an invalid shape."""


class LineWebhookReceiver:
    def __init__(
        self,
        channel_secret: str,
        *,
        event_handler: EventHandler | None = None,
    ) -> None:
        self.channel_secret = channel_secret
        self.event_handler = event_handler or log_line_event

    def receive(self, body: bytes, signature: str | None) -> int:
        if not signature or not verify_line_signature(
            body, signature, self.channel_secret
        ):
            raise InvalidWebhookSignature("invalid LINE webhook signature")

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidWebhookPayload("webhook body is not valid JSON") from error

        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise InvalidWebhookPayload("webhook payload must contain an events list")

        events = payload["events"]
        if not all(isinstance(event, dict) for event in events):
            raise InvalidWebhookPayload("every webhook event must be an object")

        for event in events:
            self.event_handler(event)
        return len(events)


def verify_line_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    digest = hmac.new(
        channel_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


def log_line_event(event: dict[str, Any]) -> None:
    logger = logging.getLogger(__name__)
    message = event.get("message")
    source = event.get("source")
    delivery_context = event.get("deliveryContext")
    request_id = (
        getattr(g, "webhook_request_id", None) if has_request_context() else None
    )
    log_event(
        logger,
        logging.INFO,
        "line_webhook_event_received",
        event_type=_string_field(event, "type"),
        webhook_event_id=_string_field(event, "webhookEventId"),
        message_type=(
            _string_field(message, "type") if isinstance(message, dict) else None
        ),
        source_type=(
            _string_field(source, "type") if isinstance(source, dict) else None
        ),
        is_redelivery=(
            delivery_context.get("isRedelivery")
            if isinstance(delivery_context, dict)
            and isinstance(delivery_context.get("isRedelivery"), bool)
            else None
        ),
        request_id=request_id,
    )


def create_app(
    settings: WebhookSettings | None = None,
    *,
    event_handler: EventHandler | None = None,
) -> Flask:
    resolved_settings = settings or WebhookSettings.from_env()
    configure_logging(resolved_settings.log_level)
    log_event(
        logging.getLogger(__name__),
        logging.INFO,
        "webhook_app_started",
        log_level=resolved_settings.log_level,
        endpoints=["/callback", "/webhooks/line"],
        health_endpoint="/health",
    )
    receiver = LineWebhookReceiver(
        resolved_settings.line_channel_secret,
        event_handler=event_handler,
    )

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_WEBHOOK_BODY_BYTES

    @app.before_request
    def log_request_started() -> None:
        g.webhook_request_id = str(uuid.uuid4())
        g.webhook_started_at = perf_counter()
        g.webhook_event_count = None
        request_kind = "health" if request.path == "/health" else "line_webhook"
        log_event(
            logging.getLogger(__name__),
            logging.INFO,
            "webhook_request_started",
            request_id=g.webhook_request_id,
            request_kind=request_kind,
            method=request.method,
            path=request.path,
            content_length=request.content_length,
            content_type=request.content_type,
            has_signature=bool(request.headers.get("x-line-signature")),
        )

    @app.after_request
    def log_request_completed(response: Response) -> Response:
        started_at = getattr(g, "webhook_started_at", None)
        log_event(
            logging.getLogger(__name__),
            logging.INFO,
            "webhook_request_completed",
            request_id=getattr(g, "webhook_request_id", None),
            request_kind=(
                "health" if request.path == "/health" else "line_webhook"
            ),
            method=request.method,
            path=request.path,
            status_code=response.status_code,
            event_count=getattr(g, "webhook_event_count", None),
            duration_ms=duration_ms(started_at) if started_at is not None else None,
        )
        return response

    @app.teardown_request
    def log_request_failed(error: BaseException | None) -> None:
        if error is None:
            return
        started_at = getattr(g, "webhook_started_at", None)
        log_event(
            logging.getLogger(__name__),
            logging.ERROR,
            "webhook_request_failed",
            request_id=getattr(g, "webhook_request_id", None),
            request_kind=(
                "health" if request.path == "/health" else "line_webhook"
            ),
            method=request.method,
            path=request.path,
            error=str(error),
            error_type=type(error).__name__,
            duration_ms=duration_ms(started_at) if started_at is not None else None,
        )

    @app.get("/health")
    def health() -> Response:
        return Response(status=204)

    @app.post("/callback")
    @app.post("/webhooks/line")
    def line_webhook() -> Response:
        body = request.get_data(cache=False, as_text=False)
        signature = request.headers.get("x-line-signature")
        request_id = getattr(g, "webhook_request_id", None)
        try:
            event_count = receiver.receive(body, signature)
            g.webhook_event_count = event_count
        except InvalidWebhookSignature:
            log_event(
                logging.getLogger(__name__),
                logging.WARNING,
                "line_webhook_signature_rejected",
                request_id=request_id,
            )
            return Response(status=401)
        except InvalidWebhookPayload as error:
            log_event(
                logging.getLogger(__name__),
                logging.WARNING,
                "line_webhook_payload_rejected",
                error=str(error),
                request_id=request_id,
            )
            return Response(status=400)
        except Exception:
            logging.getLogger(__name__).exception(
                "line_webhook_handler_failed",
                extra={
                    "event": "line_webhook_handler_failed",
                    "request_id": request_id,
                },
            )
            return Response(status=500)

        log_event(
            logging.getLogger(__name__),
            logging.INFO,
            "line_webhook_accepted",
            event_count=event_count,
            request_id=request_id,
        )
        return Response(status=200)

    return app


def _string_field(value: dict[str, Any], field: str) -> str | None:
    candidate = value.get(field)
    return candidate if isinstance(candidate, str) else None
