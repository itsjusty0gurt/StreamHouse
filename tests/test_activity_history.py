import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from twitch.activity_history import ActivityHistoryStore, PersistedActivity


class ActivityHistoryStoreTests(unittest.TestCase):
    def test_round_trip_keeps_newest_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            store = ActivityHistoryStore(path)
            for index in range(store.LIMIT + 5):
                store.add(
                    PersistedActivity(
                        category="Follows",
                        text=f"Viewer {index} followed",
                        color="#bf94ff",
                        occurred_at=datetime(
                            2026, 7, 12, 12, 0, tzinfo=timezone.utc
                        ).isoformat(),
                    )
                )

            restored = ActivityHistoryStore(path)
            entries = restored.load()
            self.assertEqual(len(entries), store.LIMIT)
            self.assertEqual(entries[0].text, "Viewer 204 followed")
            self.assertEqual(entries[-1].text, "Viewer 5 followed")

    def test_skips_malformed_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            path.write_text(
                json.dumps(
                    {
                        "events": [
                            {"text": "bad date", "occurred_at": "nope"},
                            {
                                "category": "Raids",
                                "text": "A raid",
                                "color": "#fff",
                                "occurred_at": "2026-07-12T12:00:00+00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            entries = ActivityHistoryStore(path).load()
            self.assertEqual([entry.text for entry in entries], ["A raid"])

    def test_display_uses_elapsed_time_bands(self) -> None:
        now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        cases = (
            (30, "just now"),
            (20 * 60, "20m ago"),
            (3 * 60 * 60, "3h ago"),
            (2 * 24 * 60 * 60, "2d ago"),
            (7 * 24 * 60 * 60, "7d ago"),
            (15 * 24 * 60 * 60, "2w ago"),
        )
        for elapsed_seconds, expected in cases:
            occurred_at = datetime.fromtimestamp(
                now.timestamp() - elapsed_seconds,
                timezone.utc,
            ).isoformat()
            entry = PersistedActivity(
                "Raids",
                "A raid",
                "#fff",
                occurred_at,
            )
            with self.subTest(expected=expected):
                self.assertIn(expected, entry.display_text(now))

    def test_refresh_interval_slows_as_newest_event_ages(self) -> None:
        now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        store = ActivityHistoryStore(Path("unused.json"))
        cases = (
            (20 * 60, store.MINUTE_MS),
            (3 * 60 * 60, store.HOUR_MS),
            (2 * 24 * 60 * 60, store.DAY_MS),
            (8 * 24 * 60 * 60, store.WEEK_MS),
        )
        for elapsed_seconds, expected in cases:
            store.entries = [
                PersistedActivity(
                    "Raids",
                    "A raid",
                    "#fff",
                    datetime.fromtimestamp(
                        now.timestamp() - elapsed_seconds,
                        timezone.utc,
                    ).isoformat(),
                )
            ]
            with self.subTest(expected=expected):
                self.assertEqual(store.refresh_interval_ms(now), expected)

        store.entries = []
        self.assertIsNone(store.refresh_interval_ms(now))


if __name__ == "__main__":
    unittest.main()
