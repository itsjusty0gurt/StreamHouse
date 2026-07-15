import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from twitch.auth import TwitchToken
from ui.companion_worker import CompanionRefreshWorker


class CompanionRefreshWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_collects_snapshot_chatters_and_roles(self) -> None:
        helix = Mock()
        helix.get_companion_snapshot.return_value = {"stream": None}
        helix.get_chatters.return_value = [
            {"user_id": "1", "user_name": "Viewer"}
        ]
        helix.get_chat_roles.return_value = ({"1"}, set(), set())
        token = TwitchToken(
            access_token="token",
            refresh_token="refresh",
            expires_at=9999999999,
            scopes=(
                "moderator:read:chatters",
                "moderation:read",
                "channel:read:vips",
                "channel:read:subscriptions",
            ),
            user_id="42",
            login="streamer",
        )
        completed = Mock()
        worker = CompanionRefreshWorker(7, helix, "42", token)
        worker.signals.completed.connect(completed)

        worker.run()
        self.application.processEvents()

        result = completed.call_args.args[0]
        self.assertEqual(result.request_id, 7)
        self.assertEqual(result.chatters[0]["user_name"], "Viewer")
        self.assertEqual(result.moderator_ids, frozenset({"1"}))

    def test_reports_errors_without_raising(self) -> None:
        helix = Mock()
        helix.get_companion_snapshot.side_effect = RuntimeError("offline")
        token = TwitchToken(
            access_token="token",
            refresh_token="",
            expires_at=9999999999,
            scopes=(),
            user_id="42",
            login="streamer",
        )
        failed = Mock()
        worker = CompanionRefreshWorker(3, helix, "42", token)
        worker.signals.failed.connect(failed)

        worker.run()
        self.application.processEvents()

        failed.assert_called_once_with(3, "offline")

    def test_optional_chatter_failure_preserves_snapshot(self) -> None:
        helix = Mock()
        helix.get_companion_snapshot.return_value = {
            "stream": None,
            "followers": 10,
            "subscribers": None,
            "warnings": ["subscribers: HTTP Error 401: Unauthorized"],
        }
        helix.get_chatters.side_effect = RuntimeError("HTTP Error 401")
        token = TwitchToken(
            "token",
            "refresh",
            9999999999,
            ["moderator:read:chatters"],
            "42",
            "streamer",
        )
        completed = Mock()
        worker = CompanionRefreshWorker(9, helix, "42", token)
        worker.signals.completed.connect(completed)

        worker.run()
        self.application.processEvents()

        result = completed.call_args.args[0]
        self.assertEqual(result.snapshot["followers"], 10)
        self.assertFalse(result.can_read_chatters)
        self.assertEqual(len(result.warnings), 2)


if __name__ == "__main__":
    unittest.main()
