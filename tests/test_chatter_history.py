import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from twitch.chatter_history import ChatterHistoryStore


class ChatterHistoryStoreTests(unittest.TestCase):
    def test_records_messages_snapshots_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatters.json"
            store = ChatterHistoryStore(path)
            observed_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
            store.observe_message("1", "Viewer", observed_at)
            store.observe_snapshot(
                [{"user_id": "1", "user_name": "Viewer"}],
                observed_at,
            )
            store.observe_snapshot(
                [{"user_id": "1", "user_name": "Viewer"}],
                observed_at + timedelta(hours=1),
            )
            store.save()

            restored = ChatterHistoryStore(path)
            restored.load()
            record = restored.records["1"]
            self.assertEqual(record.message_count, 1)
            self.assertEqual(record.snapshot_days, 1)
            self.assertEqual(record.active_days, ["2026-07-01"])

    def test_regular_requires_repeat_days_and_participation(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        for day in range(store.REGULAR_ACTIVE_DAYS):
            when = start + timedelta(days=day)
            store.observe_message("1", "Viewer", when)
        self.assertFalse(store.is_regular("1"))

        for message in range(
            store.REGULAR_MESSAGES - store.REGULAR_ACTIVE_DAYS
        ):
            store.observe_message("1", "Viewer", start)
        self.assertTrue(store.is_regular("1"))

    def test_rejects_invalid_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatters.json"
            path.write_text(json.dumps([]), encoding="utf-8")
            store = ChatterHistoryStore(path)
            with self.assertRaises(ValueError):
                store.load()

    def test_bot_identity_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatters.json"
            store = ChatterHistoryStore(path)
            store.observe_message("bot-1", "HelperBot", is_bot=True)
            store.save()

            restored = ChatterHistoryStore(path)
            restored.load()
            self.assertTrue(restored.is_bot("bot-1"))

    def test_snapshot_persists_all_observed_roles(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_snapshot(
            [{"user_id": "1", "user_name": "Viewer"}],
            moderator_ids={"1"},
            vip_ids={"1"},
            subscriber_ids={"1"},
        )
        self.assertEqual(
            store.records["1"].roles,
            ["Moderator", "VIP", "Subscriber"],
        )

    def test_follow_timestamp_is_recorded(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.record_follow(
            "1",
            "Viewer",
            "2026-07-01T12:00:00+00:00",
        )
        self.assertEqual(
            store.records["1"].followed_at,
            "2026-07-01T12:00:00+00:00",
        )

    def test_manual_memory_lifecycle(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("1", "Viewer")
        memory = store.add_memory("1", "Likes puzzle games", "Preference")
        self.assertEqual(memory["source"], "manual")

        updated = store.update_memory(
            "1",
            str(memory["id"]),
            text="Likes difficult puzzle games",
            pinned=True,
        )
        self.assertTrue(updated["pinned"])
        self.assertIn("difficult", str(updated["text"]))

        store.delete_memory("1", str(memory["id"]))
        self.assertEqual(store.records["1"].memories, [])

    def test_tracks_session_messages_events_and_profile(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        occurred_at = datetime(2026, 7, 12, tzinfo=timezone.utc)
        store.observe_message(
            "1",
            "Viewer",
            occurred_at,
            session_id="session-1",
        )
        store.record_event(
            "1",
            "Viewer",
            "channel.cheer",
            "Viewer cheered 100 bits",
            occurred_at,
            "session-1",
        )
        store.update_profile(
            "1",
            ("friend", " community member "),
            "Met through puzzle streams.",
        )

        record = store.records["1"]
        self.assertEqual(record.session_messages, {"session-1": 1})
        self.assertEqual(record.timeline[0]["type"], "channel.cheer")
        self.assertEqual(record.tags, ["friend", "community member"])
        self.assertIn("puzzle", record.private_notes)

    def test_merge_combines_viewer_histories(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("old", "OldName", session_id="one")
        store.observe_message("new", "NewName", session_id="two")
        store.update_profile("old", ("friend",), "Old note")

        store.merge_records("old", "new")

        self.assertNotIn("old", store.records)
        merged = store.records["new"]
        self.assertEqual(merged.message_count, 2)
        self.assertEqual(set(merged.session_messages), {"one", "two"})
        self.assertEqual(merged.tags, ["friend"])

    def test_engagement_streak_counts_consecutive_days(self) -> None:
        self.assertEqual(
            ChatterHistoryStore.engagement_streak(
                ("2026-07-10", "2026-07-11", "2026-07-12")
            ),
            3,
        )


if __name__ == "__main__":
    unittest.main()
