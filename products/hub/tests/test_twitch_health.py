import unittest
from datetime import datetime, timedelta, timezone

from products.hub.twitch.health import TwitchHealth


class TwitchHealthTests(unittest.TestCase):
    def test_channel_snapshot_tracks_success_and_warning(self) -> None:
        health = TwitchHealth()
        health.channel_snapshot_succeeded(("followers: unauthorized",))
        self.assertIsNotNone(health.last_channel_snapshot_success)
        self.assertIn("followers", health.last_channel_snapshot_error)

    def test_elapsed_text_is_human_readable(self) -> None:
        value = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.assertEqual(TwitchHealth.elapsed_text(value), "5m ago")


if __name__ == "__main__":
    unittest.main()
