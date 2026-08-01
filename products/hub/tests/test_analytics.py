import unittest
from datetime import datetime, timedelta, timezone

from products.hub.twitch.analytics import build_analytics
from products.hub.twitch.chatter_history import ChatterRecord
from products.hub.twitch.session_history import StreamSession


class AnalyticsTests(unittest.TestCase):
    def test_aggregates_sessions_and_viewers(self) -> None:
        now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        sessions = (
            StreamSession(
                started_at=(now - timedelta(hours=2)).isoformat(),
                ended_at=(now - timedelta(hours=1)).isoformat(),
                peak_viewers=20,
                messages=120,
                follows=3,
                subscriptions=2,
                cheers=1,
                raids=1,
            ),
            StreamSession(
                started_at=(now - timedelta(days=40)).isoformat(),
                ended_at=(now - timedelta(days=40, hours=-2)).isoformat(),
                peak_viewers=50,
                messages=300,
            ),
        )
        chatters = (
            ChatterRecord(
                "1",
                "Regular",
                (now - timedelta(days=6)).isoformat(),
                now.isoformat(),
                active_days=[f"day-{index}" for index in range(5)],
                message_count=30,
            ),
            ChatterRecord(
                "2",
                "NewViewer",
                now.isoformat(),
                now.isoformat(),
                active_days=["today"],
                message_count=2,
            ),
        )

        snapshot = build_analytics(sessions, chatters, days=30, now=now)

        self.assertEqual(snapshot.session_count, 1)
        self.assertEqual(snapshot.total_hours, 1)
        self.assertEqual(snapshot.total_messages, 120)
        self.assertEqual(snapshot.average_peak_viewers, 20)
        self.assertEqual(snapshot.new_viewers, 2)
        self.assertEqual(snapshot.returning_viewers, 1)
        self.assertEqual(snapshot.regular_viewers, 1)
        self.assertEqual(snapshot.top_viewers[0].user_name, "Regular")

    def test_empty_analytics_uses_zeroes(self) -> None:
        snapshot = build_analytics((), ())
        self.assertEqual(snapshot.session_count, 0)
        self.assertEqual(snapshot.messages_per_hour, 0)
        self.assertEqual(snapshot.highest_peak_viewers, 0)


if __name__ == "__main__":
    unittest.main()
