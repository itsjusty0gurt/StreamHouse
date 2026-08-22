import json
import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from products.hub.twitch.chatter_history import ChatterHistoryStore


class ChatterHistoryStoreTests(unittest.TestCase):
    @staticmethod
    def qualify(store: ChatterHistoryStore, user_id: str = "1") -> None:
        store.opt_in_memory(user_id, "Viewer")
        for index in range(store.MEMORY_REGULAR_STREAMS):
            store.record_memory_stream(user_id, f"stream-{index}")

    def test_records_messages_snapshots_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatters.json"
            store = ChatterHistoryStore(path)
            observed_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
            store.opt_in_memory("1", "Viewer", consented_at=observed_at)
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

    def test_manual_groups_persist_without_memory_consent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatters.json"
            store = ChatterHistoryStore(path)
            store.observe_message("1", "Viewer")
            store.set_manual_group("1", "Regulars")
            store.observe_message("2", "HelperBot")
            store.set_manual_group("2", "Bots")
            store.save()

            restored = ChatterHistoryStore(path)
            restored.load()
            self.assertEqual(restored.records["1"].manual_group, "Regulars")
            self.assertEqual(restored.records["2"].manual_group, "Bots")
            self.assertTrue(restored.is_bot("2"))
            self.assertEqual(len(restored.records), 2)

            with self.assertRaises(ValueError):
                restored.set_manual_group("1", "Moderators")

    def test_manual_group_removal_and_move_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatters.json"
            store = ChatterHistoryStore(path)
            store.observe_message("1", "Viewer")
            store.set_manual_group("1", "Bots")
            store.save()

            restored = ChatterHistoryStore(path)
            restored.load()
            restored.set_manual_group("1", "Viewers")
            restored.save()

            moved = ChatterHistoryStore(path)
            moved.load()
            self.assertEqual(moved.records["1"].manual_group, "Viewers")
            self.assertFalse(moved.is_bot("1"))
            moved.set_manual_group("1", "")
            moved.save()

            removed = ChatterHistoryStore(path)
            removed.load()
            self.assertNotIn("1", removed.records)

    def test_twitch_refresh_updates_name_without_erasing_local_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatters.json"
            store = ChatterHistoryStore(path)
            store.observe_snapshot(
                [{"user_id": "stable-id", "user_name": "OldName"}],
                moderator_ids={"stable-id"},
            )
            store.set_manual_group("stable-id", "Bots")
            store.observe_snapshot(
                [{"user_id": "stable-id", "user_name": "NewName"}],
                vip_ids={"stable-id"},
            )
            store.save()

            restored = ChatterHistoryStore(path)
            restored.load()
            record = restored.records["stable-id"]
            self.assertEqual(record.user_id, "stable-id")
            self.assertEqual(record.user_name, "NewName")
            self.assertEqual(record.roles, ["VIP"])
            self.assertEqual(record.manual_group, "Bots")
            self.assertTrue(restored.is_bot("stable-id"))

    def test_load_uses_storage_key_as_identity_and_resets_bad_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatters.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 6,
                        "chatters": {
                            "stable-id": {
                                "user_id": "display-name-key",
                                "user_name": "Viewer",
                                "first_seen": "",
                                "last_seen": "",
                                "manual_group": "Unknown Group",
                            },
                            "": {"user_id": "invalid"},
                            "bad-record": "not-an-object",
                        },
                    }
                ),
                encoding="utf-8",
            )

            store = ChatterHistoryStore(path)
            store.load()

            self.assertEqual(list(store.records), ["stable-id"])
            self.assertEqual(store.records["stable-id"].user_id, "stable-id")
            self.assertEqual(store.records["stable-id"].manual_group, "")
            self.assertTrue(store.dirty)
            store.save()

            cleaned = ChatterHistoryStore(path)
            cleaned.load()
            self.assertEqual(cleaned.records, {})

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
        self.qualify(store)
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

    def test_generated_memory_review_duplicate_and_conflict(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("1", "Viewer")
        self.qualify(store)
        first = store.propose_memory(
            "1",
            "Favorite game is Portal 2",
            "Game",
            confidence=0.8,
            evidence=({"text": "Portal 2 is my favorite"},),
            key="favorite-game",
        )
        self.assertEqual(first["status"], "pending")
        store.review_memory("1", str(first["id"]), True)

        duplicate = store.propose_memory(
            "1",
            "Favorite game is Portal 2",
            "Game",
            confidence=0.95,
            evidence=({"text": "Still Portal 2"},),
            key="favorite-game",
        )
        self.assertIs(duplicate, first)
        self.assertEqual(len(duplicate["evidence"]), 2)
        self.assertEqual(duplicate["confidence"], 0.95)

        replacement = store.propose_memory(
            "1",
            "Favorite game is The Witness",
            "Game",
            confidence=0.7,
            key="favorite-game",
        )
        self.assertEqual(replacement["conflicts_with"], first["id"])
        store.review_memory("1", str(replacement["id"]), True)
        self.assertEqual(first["status"], "superseded")
        self.assertTrue(first["archived"])

    def test_memory_privacy_summary_and_relevance(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("1", "Viewer")
        self.qualify(store)
        puzzle = store.add_memory("1", "Enjoys difficult puzzle games", "Preference")
        store.add_memory("1", "Drinks green tea", "Preference")
        store.update_memory("1", str(puzzle["id"]), pinned=True)

        summary = store.viewer_summary("1")
        self.assertIn("Viewer", summary)
        self.assertIn("puzzle", summary)
        relevant = store.relevant_memories("1", "recommend a puzzle game")
        self.assertEqual(relevant[0]["id"], puzzle["id"])

        pending = store.propose_memory("1", "Owns a cat", key="pet")
        store.set_memory_enabled("1", False)
        self.assertEqual(pending["status"], "rejected")
        self.assertEqual(store.approved_memories("1"), [])
        with self.assertRaises(PermissionError):
            store.propose_memory("1", "Likes jazz")

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
        store.set_manual_group("old", "Bots")

        store.merge_records("old", "new")

        self.assertNotIn("old", store.records)
        merged = store.records["new"]
        self.assertEqual(merged.message_count, 2)
        self.assertEqual(set(merged.session_messages), {"one", "two"})
        self.assertEqual(merged.tags, ["friend"])
        self.assertEqual(merged.manual_group, "Bots")
        self.assertTrue(store.is_bot("new"))

    def test_engagement_streak_counts_consecutive_days(self) -> None:
        self.assertEqual(
            ChatterHistoryStore.engagement_streak(
                ("2026-07-10", "2026-07-11", "2026-07-12")
            ),
            3,
        )

    def test_unconsented_viewer_is_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatters.json"
            store = ChatterHistoryStore(path)
            store.observe_message("viewer", "Viewer")
            store.save()

            restored = ChatterHistoryStore(path)
            restored.load()
            self.assertNotIn("viewer", restored.records)

    def test_consent_and_five_distinct_streams_unlock_keynotes(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("1", "Viewer")
        self.assertFalse(store.can_create_keynotes("1"))

        store.opt_in_memory("1", "Viewer")
        for index in range(store.MEMORY_REGULAR_STREAMS - 1):
            store.record_memory_stream("1", f"stream-{index}")
            store.record_memory_stream("1", f"stream-{index}")
        self.assertFalse(store.can_create_keynotes("1"))

        store.record_memory_stream("1", "stream-final")
        self.assertTrue(store.can_create_keynotes("1"))

    def test_daily_memory_expires_after_reset_but_survives_same_live_stream(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.opt_in_memory("1", "Viewer")
        message_time = datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc)
        store.record_daily_memory(
            "1",
            speaker="viewer",
            viewer="Viewer",
            message="Remember this for today.",
            timestamp=message_time,
            stream_id="stream-1",
        )
        now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(
            store.expire_daily_memories(
                now=now,
                reset_at=time(4, 0),
                active_stream_id="stream-1",
            ),
            0,
        )
        self.assertEqual(len(store.records["1"].daily_memory), 1)

        self.assertEqual(
            store.expire_daily_memories(
                now=now,
                reset_at=time(4, 0),
                active_stream_id="",
            ),
            1,
        )
        self.assertEqual(store.records["1"].daily_memory, [])

    def test_opt_out_and_complete_delete_remove_memory_data(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        self.qualify(store)
        store.add_memory("1", "Likes puzzle games", "Preference")
        store.record_daily_memory(
            "1", speaker="viewer", viewer="Viewer", message="Hello"
        )

        store.opt_out_memory("1")
        record = store.records["1"]
        self.assertEqual(record.memory_consent, "opted_out")
        self.assertEqual(record.memories, [])
        self.assertEqual(record.daily_memory, [])
        self.assertEqual(record.active_days, [])

        self.assertTrue(store.delete_viewer_data("1"))
        self.assertNotIn("1", store.records)


if __name__ == "__main__":
    unittest.main()
