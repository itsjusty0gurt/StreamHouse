import logging
import unittest
from unittest.mock import Mock, patch

from core.events import Events
from core.logger import Logger
from twitch.auth import TwitchToken
from twitch.models import TwitchEventTransport
from twitch.service import TwitchConnectionState, TwitchService
from twitch.simulator import create_eventsub_notification


class TwitchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_logger = Logger._logger
        Logger._logger = logging.Logger("TwitchServiceTest", logging.DEBUG)
        Logger._logger.addHandler(logging.NullHandler())
        Events.clear()
        self.service = TwitchService()

    def tearDown(self) -> None:
        self.service.disconnect()
        Events.clear()
        Logger._logger = self.original_logger

    def test_connect_emits_state_changes_and_normalizes_channel(self) -> None:
        states: list[tuple[TwitchConnectionState, str]] = []

        def receive_status(
            state: TwitchConnectionState,
            channel: str,
        ) -> None:
            states.append((state, channel))

        Events.subscribe("twitch_status_changed", receive_status)

        self.assertTrue(self.service.connect(" #ItsJustY0gurt "))
        self.assertEqual(self.service.channel, "itsjusty0gurt")
        self.assertEqual(self.service.state, TwitchConnectionState.CONNECTED)
        self.assertTrue(self.service.listener_url.startswith("http://127.0.0.1:"))
        self.assertEqual(
            states,
            [
                (TwitchConnectionState.CONNECTING, "itsjusty0gurt"),
                (TwitchConnectionState.CONNECTED, "itsjusty0gurt"),
            ],
        )

    @patch("twitch.service.TwitchEventSubSocket")
    def test_authenticated_connect_uses_live_eventsub(self, socket_type: Mock) -> None:
        token = TwitchToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=999,
            scopes=["user:read:chat"],
            user_id="bot-1",
            login="sallybot",
        )
        auth = Mock(token=token)
        helix = Mock()
        helix.get_user.return_value = {"id": "channel-1", "login": "streamer"}
        helix.get_badge_urls.return_value = {
            ("moderator", "1"): "https://example.test/mod.png"
        }
        service = TwitchService(auth=auth, helix=helix)

        try:
            self.assertTrue(service.connect("Streamer"))
            self.assertIs(service.state, TwitchConnectionState.CONNECTING)
            socket_type.return_value.open.assert_called_once_with()

            service._receive_live_welcome("session-1")

            helix.create_chat_subscriptions.assert_called_once_with(
                "session-1", "channel-1", "bot-1", token
            )
            helix.create_activity_subscriptions.assert_called_once_with(
                "session-1", "channel-1", "bot-1", token
            )
            self.assertIs(service.state, TwitchConnectionState.CONNECTED)
            self.assertEqual(
                service.badge_url("moderator", "1"),
                "https://example.test/mod.png",
            )

            self.assertTrue(service.send_message("Hello chat!"))
            helix.send_chat_message.assert_called_once_with(
                "channel-1", "bot-1", "Hello chat!", token
            )
        finally:
            service.disconnect()

    @patch("twitch.service.TwitchEventSubSocket")
    def test_separate_bot_token_reads_and_sends_chat(self, socket_type: Mock) -> None:
        broadcaster = TwitchToken(
            "broadcaster-access",
            "refresh",
            999,
            ["channel:bot", "channel:read:subscriptions"],
            user_id="channel-1",
            login="streamer",
        )
        bot = TwitchToken(
            "bot-access",
            "refresh",
            999,
            ["user:read:chat", "user:write:chat", "user:bot"],
            user_id="bot-1",
            login="sallybot",
        )
        helix = Mock()
        helix.get_user.return_value = {"id": "channel-1"}
        helix.get_badge_urls.return_value = {}
        service = TwitchService(
            auth=Mock(token=broadcaster),
            bot_auth=Mock(token=bot),
            helix=helix,
        )

        try:
            self.assertTrue(service.connect("streamer"))
            service._receive_live_welcome("session-1")
            service._receive_activity_welcome("activity-session-1")
            self.assertTrue(service.send_message("Hello from Sally"))

            helix.create_chat_subscriptions.assert_called_once_with(
                "session-1", "channel-1", "bot-1", bot
            )
            helix.create_activity_subscriptions.assert_called_once_with(
                "activity-session-1",
                "channel-1",
                "channel-1",
                broadcaster,
            )
            self.assertEqual(socket_type.return_value.open.call_count, 2)
            helix.send_chat_message.assert_called_once_with(
                "channel-1", "bot-1", "Hello from Sally", bot
            )
            self.assertTrue(
                service.send_message("Hello from streamer", as_bot=False)
            )
            self.assertEqual(
                helix.send_chat_message.call_args_list[-1],
                unittest.mock.call(
                    "channel-1",
                    "channel-1",
                    "Hello from streamer",
                    broadcaster,
                ),
            )
        finally:
            service.disconnect()

    def test_same_account_cannot_fill_broadcaster_and_bot_slots(self) -> None:
        token = TwitchToken(
            "access",
            "refresh",
            999,
            ["user:read:chat", "user:write:chat"],
            user_id="same-1",
            login="sallybot",
        )
        helix = Mock()
        helix.get_user.return_value = {"id": "same-1"}
        service = TwitchService(
            auth=Mock(token=token),
            bot_auth=Mock(token=token),
            helix=helix,
        )
        errors = []
        Events.subscribe(
            "twitch_error",
            lambda message: errors.append(message),
        )

        self.assertFalse(service.connect("sallybot"))
        self.assertIn("same Twitch account", errors[0])

    def test_chat_moderation_notification_emits_notice(self) -> None:
        notices = []
        Events.subscribe(
            "twitch_notice_received",
            lambda notice: notices.append(notice),
        )

        self.service._receive_notification(
            "channel.chat.message_delete",
            {
                "event": {
                    "target_user_login": "viewer",
                    "message_id": "message-1",
                }
            },
        )

        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].kind, "delete")
        self.assertEqual(notices[0].target_message_id, "message-1")

    def test_moderation_actions_use_signed_in_identity(self) -> None:
        token = TwitchToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=999,
            scopes=["moderator:manage:banned_users"],
            user_id="moderator-1",
            login="streamer",
        )
        helix = Mock()
        service = TwitchService(auth=Mock(token=token), helix=helix)
        service.broadcaster_user_id = "channel-1"

        self.assertTrue(
            service.moderate_user(
                "timeout", "viewer-1", duration=600, reason="spam"
            )
        )
        helix.ban_user.assert_called_once_with(
            "channel-1",
            "moderator-1",
            "viewer-1",
            token,
            duration=600,
            reason="spam",
        )

    def test_disconnect_returns_to_disconnected(self) -> None:
        self.service.connect("channel")

        self.assertTrue(self.service.disconnect())
        self.assertEqual(
            self.service.state,
            TwitchConnectionState.DISCONNECTED,
        )
        self.assertEqual(self.service.channel, "")
        self.assertEqual(self.service.listener_url, "")

    def test_simulated_message_emits_typed_message(self) -> None:
        messages = []

        def receive_message(chat_message) -> None:
            messages.append(chat_message)

        Events.subscribe("twitch_message_received", receive_message)
        self.service.connect("channel")

        self.assertTrue(self.service.simulate_message(" viewer ", " Hi! "))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].username, "viewer")
        self.assertEqual(messages[0].text, "Hi!")
        self.assertIsNotNone(messages[0].received_at.tzinfo)
        self.assertTrue(messages[0].message_id)
        self.assertEqual(messages[0].fragments[0].text, "Hi!")

    def test_invalid_operations_emit_errors(self) -> None:
        errors: list[str] = []

        def receive_error(message: str) -> None:
            errors.append(message)

        Events.subscribe("twitch_error", receive_error)

        self.assertFalse(self.service.connect("  "))
        self.assertFalse(self.service.simulate_message("viewer", "hello"))
        self.assertEqual(len(errors), 2)
        self.assertEqual(self.service.state, TwitchConnectionState.ERROR)

    def test_invalid_message_does_not_drop_active_connection(self) -> None:
        self.service.connect("channel")

        self.assertFalse(self.service.simulate_message("", "hello"))
        self.assertEqual(self.service.state, TwitchConnectionState.CONNECTED)
        self.assertEqual(self.service.channel, "channel")

    def test_generic_event_uses_signed_local_listener(self) -> None:
        notifications = []
        generic_bus_events = []
        typed_bus_events = []

        def receive_notification(subscription_type: str, payload: dict) -> None:
            notifications.append((subscription_type, payload))

        Events.subscribe("twitch_notification_received", receive_notification)
        Events.subscribe(
            "twitch_event",
            lambda twitch_event: generic_bus_events.append(twitch_event),
        )
        Events.subscribe(
            "twitch_event.channel.follow",
            lambda twitch_event: typed_bus_events.append(twitch_event),
        )
        self.service.connect("channel")
        payload = create_eventsub_notification(
            "channel.follow",
            "2",
            "channel",
        )

        self.assertTrue(
            self.service.simulate_event("channel.follow", "2", payload)
        )
        self.assertEqual(notifications[0][0], "channel.follow")
        self.assertIn("followed_at", notifications[0][1]["event"])
        self.assertEqual(len(generic_bus_events), 1)
        self.assertEqual(typed_bus_events, generic_bus_events)
        bus_event = generic_bus_events[0]
        self.assertEqual(bus_event.subscription_type, "channel.follow")
        self.assertEqual(bus_event.version, "2")
        self.assertIs(bus_event.transport, TwitchEventTransport.SIMULATOR)
        self.assertTrue(bus_event.message_id)
        self.assertEqual(bus_event.payload, payload)


if __name__ == "__main__":
    unittest.main()
