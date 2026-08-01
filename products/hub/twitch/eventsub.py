from __future__ import annotations

import hashlib
import hmac
import json
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock, Thread
from typing import Any

from shared.streamhouse_runtime.logger import Logger
from products.hub.twitch.models import (
    TwitchEvent,
    TwitchEventDiagnostic,
    TwitchEventTransport,
    TwitchMessage,
)
from products.hub.twitch.parser import TwitchMessageParser, TwitchPayloadError


MESSAGE_ID_HEADER = "twitch-eventsub-message-id"
MESSAGE_TYPE_HEADER = "twitch-eventsub-message-type"
MESSAGE_SIGNATURE_HEADER = "twitch-eventsub-message-signature"
MESSAGE_TIMESTAMP_HEADER = "twitch-eventsub-message-timestamp"
SUBSCRIPTION_TYPE_HEADER = "twitch-eventsub-subscription-type"


@dataclass(frozen=True, slots=True)
class WebhookResponse:
    status: int
    body: bytes = b""
    content_type: str = "text/plain"


class EventSubWebhookProcessor:
    """Validate and process Twitch EventSub webhook requests."""

    def __init__(
        self,
        secret: str,
        on_chat_message: Callable[[TwitchMessage], None],
        on_revocation: Callable[[str], None] | None = None,
        on_diagnostic: Callable[[TwitchEventDiagnostic], None] | None = None,
        on_notification: Callable[[str, dict[str, Any]], None] | None = None,
        on_bus_event: Callable[[TwitchEvent], None] | None = None,
        transport: TwitchEventTransport = TwitchEventTransport.SIMULATOR,
        duplicate_cache_size: int = 1000,
    ) -> None:
        if not 10 <= len(secret) <= 100:
            raise ValueError("EventSub secret must be 10 to 100 characters.")

        self.secret = secret.encode("ascii")
        self.on_chat_message = on_chat_message
        self.on_revocation = on_revocation
        self.on_diagnostic = on_diagnostic
        self.on_notification = on_notification
        self.on_bus_event = on_bus_event
        self.transport = transport
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._duplicate_cache_size = duplicate_cache_size
        self._lock = RLock()

    def process(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> WebhookResponse:
        normalized_headers = {
            key.lower(): value for key, value in headers.items()
        }
        message_id = normalized_headers.get(MESSAGE_ID_HEADER, "")
        timestamp = normalized_headers.get(MESSAGE_TIMESTAMP_HEADER, "")
        signature = normalized_headers.get(MESSAGE_SIGNATURE_HEADER, "")
        message_type = normalized_headers.get(MESSAGE_TYPE_HEADER, "")
        subscription_type = normalized_headers.get(
            SUBSCRIPTION_TYPE_HEADER,
            "",
        )

        def finish(
            status: int,
            result: str,
            summary: str,
            payload: dict[str, Any] | None = None,
            response_body: bytes = b"",
        ) -> WebhookResponse:
            if self.on_diagnostic is not None:
                try:
                    received_at = datetime.fromisoformat(
                        timestamp.replace("Z", "+00:00")
                    )
                except ValueError:
                    received_at = datetime.now(timezone.utc)

                safe_header_names = (
                    MESSAGE_ID_HEADER,
                    MESSAGE_TYPE_HEADER,
                    MESSAGE_TIMESTAMP_HEADER,
                    SUBSCRIPTION_TYPE_HEADER,
                    "twitch-eventsub-subscription-version",
                    "twitch-eventsub-message-retry",
                )
                safe_headers = {
                    name: normalized_headers[name]
                    for name in safe_header_names
                    if name in normalized_headers
                }
                self.on_diagnostic(
                    TwitchEventDiagnostic(
                        received_at=received_at,
                        message_id=message_id,
                        message_type=message_type or "unknown",
                        subscription_type=subscription_type or "unknown",
                        result=result,
                        summary=summary,
                        status_code=status,
                        headers=safe_headers,
                        payload=payload,
                    )
                )
            return WebhookResponse(status, response_body)

        if not all((message_id, timestamp, signature, message_type)):
            return finish(400, "Rejected", "Missing required EventSub headers")

        if not self._signature_is_valid(
            message_id,
            timestamp,
            body,
            signature,
        ):
            Logger.warning(
                "Rejected EventSub request with an invalid signature.",
                source="TWITCH",
            )
            return finish(403, "Rejected", "Invalid HMAC signature")

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return finish(400, "Error", "Malformed JSON body")

        if not isinstance(payload, dict):
            return finish(400, "Error", "JSON body is not an object")

        if message_type == "webhook_callback_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str):
                return finish(400, "Error", "Missing verification challenge", payload)
            return finish(
                200,
                "Accepted",
                "Webhook verification challenge",
                payload,
                challenge.encode("utf-8"),
            )

        if message_type == "revocation":
            subscription = payload.get("subscription", {})
            status = (
                str(subscription.get("status", "unknown"))
                if isinstance(subscription, dict)
                else "unknown"
            )
            if self._is_duplicate(message_id):
                return finish(204, "Ignored", "Duplicate revocation", payload)
            if self.on_revocation is not None:
                self.on_revocation(status)
            return finish(
                204,
                "Processed",
                f"Subscription revoked: {status}",
                payload,
            )

        if message_type != "notification":
            return finish(204, "Ignored", f"Unknown message type: {message_type}", payload)

        event = payload.get("event")
        if not isinstance(event, dict):
            return finish(400, "Error", "Missing event object", payload)

        subscription = payload.get("subscription", {})
        version = (
            str(subscription.get("version", ""))
            if isinstance(subscription, dict)
            else ""
        )

        def publish_bus_event() -> None:
            if self.on_bus_event is None:
                return
            try:
                received_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                received_at = datetime.now(timezone.utc)
            self.on_bus_event(
                TwitchEvent(
                    subscription_type=subscription_type,
                    version=version,
                    received_at=received_at,
                    message_id=message_id,
                    broadcaster_user_id=str(event.get("broadcaster_user_id", "")),
                    broadcaster_user_login=str(event.get("broadcaster_user_login", "")),
                    broadcaster_user_name=str(event.get("broadcaster_user_name", "")),
                    transport=self.transport,
                    payload=payload,
                )
            )

        if subscription_type != "channel.chat.message":
            if self._is_duplicate(message_id):
                return finish(
                    204,
                    "Ignored",
                    "Duplicate notification",
                    payload,
                )
            if self.on_notification is not None:
                self.on_notification(subscription_type, payload)
            publish_bus_event()
            return finish(
                204,
                "Processed",
                f"Simulated {subscription_type or 'unknown'} notification",
                payload,
            )

        try:
            received_at = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
            chat_message = TwitchMessageParser.parse(event, received_at)
        except (ValueError, TwitchPayloadError):
            return finish(400, "Error", "Invalid chat message payload", payload)

        if self._is_duplicate(message_id):
            Logger.debug(
                f'Ignored duplicate EventSub message "{message_id}".',
                source="TWITCH",
            )
            return finish(
                204,
                "Ignored",
                "Duplicate notification",
                payload,
            )

        if self.on_notification is not None:
            self.on_notification(subscription_type, payload)
        publish_bus_event()
        self.on_chat_message(chat_message)
        return finish(
            204,
            "Processed",
            f"{chat_message.username}: {chat_message.text}",
            payload,
        )

    def _signature_is_valid(
        self,
        message_id: str,
        timestamp: str,
        body: bytes,
        supplied_signature: str,
    ) -> bool:
        signed_message = (
            message_id.encode("utf-8")
            + timestamp.encode("utf-8")
            + body
        )
        digest = hmac.new(
            self.secret,
            signed_message,
            hashlib.sha256,
        ).hexdigest()
        expected_signature = f"sha256={digest}"
        return hmac.compare_digest(expected_signature, supplied_signature)

    def _is_duplicate(self, message_id: str) -> bool:
        with self._lock:
            if message_id in self._seen_ids:
                return True

            self._seen_ids.add(message_id)
            self._seen_order.append(message_id)

            while len(self._seen_order) > self._duplicate_cache_size:
                expired_id = self._seen_order.popleft()
                self._seen_ids.discard(expired_id)

        return False


class LocalEventSubListener:
    """Run a Twitch-compatible webhook listener on localhost only."""

    MAX_BODY_SIZE = 1_048_576

    def __init__(self, processor: EventSubWebhookProcessor) -> None:
        self.processor = processor
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        if self._server is None:
            return ""
        return f"http://127.0.0.1:{self._server.server_port}/eventsub"

    def start(self) -> str:
        if self._server is not None:
            return self.url

        processor = self.processor
        max_body_size = self.MAX_BODY_SIZE

        class EventSubRequestHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != "/eventsub":
                    self.send_error(404)
                    return

                try:
                    content_length = int(self.headers.get("Content-Length", 0))
                except ValueError:
                    self.send_error(400)
                    return

                if content_length < 0 or content_length > max_body_size:
                    self.send_error(413)
                    return

                body = self.rfile.read(content_length)
                response = processor.process(dict(self.headers), body)
                self.send_response(response.status)
                self.send_header("Content-Length", str(len(response.body)))
                if response.body:
                    self.send_header("Content-Type", response.content_type)
                self.end_headers()
                if response.body:
                    self.wfile.write(response.body)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            EventSubRequestHandler,
        )
        self._thread = Thread(
            target=self._serve,
            name="SallyEventSubListener",
            daemon=True,
        )
        self._thread.start()
        Logger.info(
            f"Local EventSub listener started at {self.url}.",
            source="TWITCH",
        )
        return self.url

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        if server is None:
            return

        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)

        self._server = None
        self._thread = None
        Logger.info("Local EventSub listener stopped.", source="TWITCH")

    def _serve(self) -> None:
        if self._server is not None:
            self._server.serve_forever(poll_interval=0.05)
