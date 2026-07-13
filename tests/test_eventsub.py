import hashlib
import hmac
import json
import logging
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from core.logger import Logger
from twitch.catalog import EVENTSUB_SUBSCRIPTIONS
from twitch.eventsub import (
    EventSubWebhookProcessor,
    LocalEventSubListener,
)
from twitch.models import TwitchFragmentType
from twitch.parser import TwitchMessageParser
from twitch.simulator import (
    create_chat_notification,
    create_eventsub_notification,
    send_signed_eventsub_request,
)


SECRET = "0123456789abcdef0123456789abcdef"


def signed_headers(
    body: bytes,
    message_id: str = "message-1",
    message_type: str = "notification",
    subscription_type: str = "channel.chat.message",
) -> dict[str, str]:
    timestamp = "2026-07-11T20:00:00Z"
    signed_message = message_id.encode() + timestamp.encode() + body
    digest = hmac.new(
        SECRET.encode(),
        signed_message,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Twitch-Eventsub-Message-Id": message_id,
        "Twitch-Eventsub-Message-Type": message_type,
        "Twitch-Eventsub-Message-Signature": f"sha256={digest}",
        "Twitch-Eventsub-Message-Timestamp": timestamp,
        "Twitch-Eventsub-Subscription-Type": subscription_type,
    }


class TwitchMessageParserTests(unittest.TestCase):
    def test_parses_badges_emotes_mentions_cheers_and_replies(self) -> None:
        payload = create_chat_notification("channel", "Viewer", "Kappa @mod")
        event = payload["event"]
        event["color"] = "#00FF7F"
        event["badges"] = [
            {"set_id": "moderator", "id": "1", "info": ""},
            {"set_id": "subscriber", "id": "12", "info": "16"},
        ]
        event["message"]["fragments"] = [
            {
                "type": "emote",
                "text": "Kappa",
                "emote": {
                    "id": "25",
                    "emote_set_id": "0",
                    "owner_id": "0",
                    "format": ["static", "animated"],
                },
                "cheermote": None,
                "mention": None,
            },
            {
                "type": "text",
                "text": " ",
                "emote": None,
                "cheermote": None,
                "mention": None,
            },
            {
                "type": "mention",
                "text": "@mod",
                "emote": None,
                "cheermote": None,
                "mention": {
                    "user_id": "42",
                    "user_name": "Mod",
                    "user_login": "mod",
                },
            },
        ]
        event["reply"] = {
            "parent_message_id": "parent",
            "parent_message_body": "Earlier message",
            "parent_user_id": "9",
            "parent_user_name": "Someone",
            "parent_user_login": "someone",
            "thread_message_id": "parent",
            "thread_user_id": "9",
            "thread_user_name": "Someone",
            "thread_user_login": "someone",
        }

        message = TwitchMessageParser.parse(
            event,
            datetime.now(timezone.utc),
        )

        self.assertEqual(message.color, "#00FF7F")
        self.assertEqual(message.badges[1].info, "16")
        self.assertEqual(message.fragments[0].type, TwitchFragmentType.EMOTE)
        self.assertEqual(message.fragments[0].emote.id, "25")
        self.assertIn("animated", message.fragments[0].emote.formats)
        self.assertEqual(message.fragments[2].mention.user_login, "mod")
        self.assertEqual(message.reply.parent_message_body, "Earlier message")

    def test_official_catalog_contains_current_event_families(self) -> None:
        keys = {
            (subscription.type, subscription.version)
            for subscription in EVENTSUB_SUBSCRIPTIONS
        }
        self.assertEqual(len(keys), len(EVENTSUB_SUBSCRIPTIONS))
        self.assertGreaterEqual(len(keys), 80)
        self.assertIn(("channel.chat.message", "1"), keys)
        self.assertIn(("channel.follow", "2"), keys)
        self.assertIn(("channel.hype_train.begin", "2"), keys)
        self.assertIn(("stream.online", "1"), keys)
        self.assertIn(("user.whisper.message", "1"), keys)


class EventSubWebhookProcessorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_logger = Logger._logger
        Logger._logger = logging.Logger("EventSubTest", logging.DEBUG)
        Logger._logger.addHandler(logging.NullHandler())
        self.messages = []
        self.revocations = []
        self.diagnostics = []
        self.notifications = []
        self.processor = EventSubWebhookProcessor(
            SECRET,
            self.messages.append,
            self.revocations.append,
            self.diagnostics.append,
            lambda event_type, payload: self.notifications.append(
                (event_type, payload)
            ),
        )

    def tearDown(self) -> None:
        Logger._logger = self.original_logger

    def test_valid_notification_and_duplicate_delivery(self) -> None:
        payload = create_chat_notification("channel", "Viewer", "Hello")
        body = json.dumps(payload).encode()
        headers = signed_headers(body)

        self.assertEqual(self.processor.process(headers, body).status, 204)
        self.assertEqual(self.processor.process(headers, body).status, 204)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(
            [item.result for item in self.diagnostics],
            ["Processed", "Ignored"],
        )

    def test_invalid_signature_is_rejected(self) -> None:
        body = b"{}"
        headers = signed_headers(body)
        headers["Twitch-Eventsub-Message-Signature"] = "sha256=invalid"

        self.assertEqual(self.processor.process(headers, body).status, 403)
        self.assertEqual(self.diagnostics[0].result, "Rejected")
        self.assertNotIn(
            "twitch-eventsub-message-signature",
            self.diagnostics[0].headers,
        )

    def test_malformed_delivery_does_not_poison_duplicate_cache(self) -> None:
        malformed_body = b"not-json"
        malformed_headers = signed_headers(
            malformed_body,
            message_id="retry-id",
        )
        self.assertEqual(
            self.processor.process(malformed_headers, malformed_body).status,
            400,
        )

        payload = create_chat_notification("channel", "Viewer", "Retry")
        valid_body = json.dumps(payload).encode()
        valid_headers = signed_headers(valid_body, message_id="retry-id")
        self.assertEqual(
            self.processor.process(valid_headers, valid_body).status,
            204,
        )
        self.assertEqual(len(self.messages), 1)

    def test_verification_challenge_is_returned_raw(self) -> None:
        body = json.dumps({"challenge": "challenge-value"}).encode()
        headers = signed_headers(
            body,
            message_type="webhook_callback_verification",
        )

        response = self.processor.process(headers, body)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"challenge-value")

    def test_generic_official_event_is_processed_and_forwarded(self) -> None:
        payload = create_eventsub_notification(
            "channel.follow",
            "2",
            "channel",
        )
        body = json.dumps(payload).encode()
        headers = signed_headers(
            body,
            subscription_type="channel.follow",
        )

        response = self.processor.process(headers, body)

        self.assertEqual(response.status, 204)
        self.assertEqual(self.notifications[0][0], "channel.follow")
        self.assertIn("followed_at", self.notifications[0][1]["event"])
        self.assertEqual(self.diagnostics[0].result, "Processed")

    def test_revocation_is_reported(self) -> None:
        body = json.dumps(
            {"subscription": {"status": "authorization_revoked"}}
        ).encode()
        headers = signed_headers(body, message_type="revocation")

        self.assertEqual(self.processor.process(headers, body).status, 204)
        self.assertEqual(self.revocations, ["authorization_revoked"])


class LocalEventSubListenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_logger = Logger._logger
        Logger._logger = logging.Logger("LocalListenerTest", logging.DEBUG)
        Logger._logger.addHandler(logging.NullHandler())
        self.messages = []
        self.processor = EventSubWebhookProcessor(
            SECRET,
            self.messages.append,
        )
        self.listener = LocalEventSubListener(self.processor)
        self.listener.start()

    def tearDown(self) -> None:
        self.listener.stop()
        Logger._logger = self.original_logger

    def test_signed_http_notification_reaches_parser(self) -> None:
        payload = create_chat_notification("channel", "Viewer", "Hello")

        status = send_signed_eventsub_request(
            self.listener.url,
            SECRET,
            payload,
        )

        self.assertEqual(status, 204)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0].text, "Hello")

    def test_unknown_path_returns_404(self) -> None:
        request = Request(
            self.listener.url.replace("/eventsub", "/unknown"),
            data=b"{}",
            method="POST",
        )

        with self.assertRaises(HTTPError):
            urlopen(request, timeout=3)


if __name__ == "__main__":
    unittest.main()
