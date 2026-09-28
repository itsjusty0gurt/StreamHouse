import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from products.hub.twitch.session_history import (
    StreamSession,
    StreamSessionStore,
    StreamSessionTracker,
)


class StreamSessionHistoryTests(unittest.TestCase):
    def test_tracks_and_round_trips_completed_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            store = StreamSessionStore(path)
            tracker = StreamSessionTracker(store)
            tracker.observe_stream(
                {
                    "started_at": "2026-07-12T12:00:00+00:00",
                    "viewer_count": 12,
                }
            )
            tracker.observe_message()
            tracker.observe_event("channel.follow")
            tracker.observe_stream(
                {
                    "started_at": "2026-07-12T12:00:00+00:00",
                    "viewer_count": 25,
                }
            )
            tracker.observe_stream(None)

            restored = StreamSessionStore(path)
            restored.load()
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], StreamSessionStore.VERSION)
            session = restored.sessions[0]
            self.assertEqual(session.peak_viewers, 25)
            self.assertEqual(session.messages, 1)
            self.assertEqual(session.follows, 1)
            self.assertTrue(session.ended_at)

    def test_current_incomplete_session_survives_reload_and_can_finish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            store = StreamSessionStore(path)
            tracker = StreamSessionTracker(store)
            tracker.observe_stream(
                {
                    "started_at": "2026-08-29T12:00:00+00:00",
                    "viewer_count": 5,
                }
            )

            restored = StreamSessionStore(path)
            restored.load()
            self.assertEqual(restored.current.started_at, "2026-08-29T12:00:00+00:00")
            self.assertTrue(StreamSessionTracker(restored).observe_stream(None))
            self.assertIsNone(restored.current)
            self.assertEqual(len(restored.sessions), 1)

    def test_obsolete_unversioned_schema_is_rejected_before_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            path.write_text(json.dumps({"sessions": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "discarded pre-alpha schema"):
                StreamSessionStore(path).load()

    def test_ignores_activity_while_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StreamSessionStore(Path(directory) / "sessions.json")
            tracker = StreamSessionTracker(store)
            self.assertFalse(tracker.observe_message())
            self.assertFalse(tracker.observe_event("channel.raid"))

    def test_retention_removes_only_old_completed_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 7, 12, tzinfo=timezone.utc)
            store = StreamSessionStore(Path(directory) / "sessions.json")
            store.sessions = [
                StreamSession(
                    started_at=(now - timedelta(days=400)).isoformat(),
                    ended_at=(now - timedelta(days=399)).isoformat(),
                ),
                StreamSession(
                    started_at=(now - timedelta(days=2)).isoformat(),
                    ended_at=(now - timedelta(days=1)).isoformat(),
                ),
            ]
            removed = store.prune(365, now)
            self.assertEqual(removed, 1)
            self.assertEqual(len(store.sessions), 1)


if __name__ == "__main__":
    unittest.main()
