import unittest
from datetime import datetime, timezone

from products.hub.twitch.activity import format_twitch_activity
from products.hub.twitch.models import TwitchEvent, TwitchEventTransport


class TwitchActivityFormattingTests(unittest.TestCase):
    def make_event(self, event_type: str, event: dict) -> TwitchEvent:
        return TwitchEvent(
            subscription_type=event_type,
            version="1",
            received_at=datetime.now(timezone.utc),
            message_id="message-1",
            broadcaster_user_id="42",
            broadcaster_user_login="channel",
            broadcaster_user_name="Channel",
            transport=TwitchEventTransport.WEBSOCKET,
            payload={"event": event},
        )

    def test_formats_supported_activity_families(self) -> None:
        cases = (
            ("channel.follow", {"user_name": "Follower"}, "Follows"),
            ("channel.subscribe", {"user_name": "Sub"}, "Subscriptions"),
            ("channel.raid", {"from_broadcaster_user_name": "Raider", "viewers": 12}, "Raids"),
            ("channel.cheer", {"user_name": "Cheerer", "bits": 100}, "Cheers"),
            (
                "channel.channel_points_custom_reward_redemption.add",
                {"user_name": "Viewer", "reward": {"title": "Hydrate"}},
                "Rewards",
            ),
        )
        for event_type, payload, category in cases:
            with self.subTest(event_type=event_type):
                entry = format_twitch_activity(self.make_event(event_type, payload))
                self.assertIsNotNone(entry)
                self.assertEqual(entry.category, category)
                self.assertTrue(entry.color.startswith("#"))

    def test_ignores_non_activity_event(self) -> None:
        entry = format_twitch_activity(
            self.make_event("channel.chat.message", {})
        )
        self.assertIsNone(entry)


if __name__ == "__main__":
    unittest.main()
