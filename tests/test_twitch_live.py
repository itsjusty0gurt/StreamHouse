import json
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from twitch.auth import TwitchToken
from twitch.live import TwitchEventSubSocket, TwitchHelixClient
from twitch.models import TwitchEventTransport
from twitch.simulator import create_chat_notification


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self.payload


class TwitchHelixClientTests(unittest.TestCase):
    @patch("twitch.live.urlopen")
    def test_paginated_collection_follows_cursor(self, open_url) -> None:
        open_url.side_effect = (
            _JsonResponse(
                {
                    "data": [{"user_id": "1"}],
                    "pagination": {"cursor": "next-page"},
                }
            ),
            _JsonResponse(
                {"data": [{"user_id": "2"}], "pagination": {}}
            ),
        )
        token = TwitchToken("access", "refresh", 999, [])

        records = TwitchHelixClient()._get_paginated(
            "https://example.test/items?first=100",
            token,
        )

        self.assertEqual([item["user_id"] for item in records], ["1", "2"])
        second_url = open_url.call_args_list[1].args[0].full_url
        self.assertIn("after=next-page", second_url)


class TwitchEventSubSocketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def test_welcome_and_chat_notification_are_forwarded_once(self) -> None:
        welcomes = []
        messages = []
        notifications = []
        diagnostics = []
        errors = []
        bus_events = []
        socket = TwitchEventSubSocket(
            on_welcome=welcomes.append,
            on_message=messages.append,
            on_notification=lambda kind, payload: notifications.append(kind),
            on_diagnostic=diagnostics.append,
            on_revocation=lambda status: None,
            on_error=errors.append,
            on_bus_event=bus_events.append,
        )
        socket._receive_text(
            json.dumps(
                {
                    "metadata": {
                        "message_id": "welcome-1",
                        "message_type": "session_welcome",
                        "message_timestamp": "2026-07-12T18:00:00Z",
                    },
                    "payload": {"session": {"id": "session-1"}},
                }
            )
        )
        event_payload = create_chat_notification("channel", "viewer", "Hello!")
        live_message = {
            "metadata": {
                "message_id": "chat-1",
                "message_type": "notification",
                "message_timestamp": "2026-07-12T18:00:01Z",
                "subscription_type": "channel.chat.message",
                "subscription_version": "1",
            },
            "payload": event_payload,
        }

        socket._receive_text(json.dumps(live_message))
        socket._receive_text(json.dumps(live_message))

        self.assertEqual(welcomes, ["session-1"])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].text, "Hello!")
        self.assertEqual(notifications, ["channel.chat.message"])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(errors, [])
        self.assertEqual(len(bus_events), 1)
        self.assertEqual(bus_events[0].message_id, "chat-1")
        self.assertIs(
            bus_events[0].transport,
            TwitchEventTransport.WEBSOCKET,
        )


if __name__ == "__main__":
    unittest.main()
