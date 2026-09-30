from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from products.hub.twitch.models import TwitchMessage
from products.hub.ui.raid_landing_window import RaidLandingWindow
from products.hub.ui.raid_page import RaidCandidate


class _FakeChatSession(QObject):
    message_received = Signal(object)
    state_changed = Signal(str)

    def __init__(self, target_id: str, parent=None) -> None:
        super().__init__(parent)
        self.target_id = target_id
        self.started = 0
        self.closed = 0

    def start(self) -> None:
        self.started += 1
        self.state_changed.emit("Target chat connected — read-only in V1.")

    def close(self) -> None:
        self.closed += 1


def _candidate() -> RaidCandidate:
    return RaidCandidate(
        user_id="target-id",
        login="target_login",
        display_name="Target Display",
        category="Just Chatting",
        title="Target stream title",
        viewer_count=42,
        started_at=datetime.now(timezone.utc),
        thumbnail_url="",
    )


class RaidLandingWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.sessions: list[_FakeChatSession] = []
        self.opened_urls = []

        def create_session(target_id, parent):
            session = _FakeChatSession(target_id, parent)
            self.sessions.append(session)
            return session

        self.window = RaidLandingWindow(
            _candidate(),
            Mock(),
            url_opener=self.opened_urls.append,
            chat_session_factory=create_session,
        )

    def tearDown(self) -> None:
        self.window.shutdown()
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def test_identity_chat_and_local_video_wording(self) -> None:
        self.assertEqual(self.window.channel_name_label.full_text, "Target Display")
        self.assertEqual(self.window.channel_login_label.text(), "@target_login")
        self.assertEqual(self.window.category_label.full_text, "Just Chatting")
        self.assertEqual(self.sessions[0].target_id, "target-id")
        self.assertEqual(self.sessions[0].started, 1)
        self.assertIn("read-only", self.window.chat_status_label.text())
        self.assertIn("Open on Twitch for video", self.window.video_note_label.text())

    def test_open_on_twitch_uses_login_not_display_name(self) -> None:
        self.window.open_twitch_button.click()

        self.assertEqual(len(self.opened_urls), 1)
        self.assertEqual(
            self.opened_urls[0].toString(),
            "https://www.twitch.tv/target_login",
        )
        self.assertNotIn("Target Display", self.opened_urls[0].toString())

    def test_always_on_top_preserves_window_chat_and_session_identity(self) -> None:
        session = self.window._chat_session
        chat_view = self.window.chat_view
        self.window.show()
        self.application.processEvents()

        self.window.always_on_top_checkbox.setChecked(True)
        self.application.processEvents()
        self.assertTrue(
            bool(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        )
        self.assertIs(self.window._chat_session, session)
        self.assertIs(self.window.chat_view, chat_view)

        self.window.always_on_top_checkbox.setChecked(False)
        self.application.processEvents()
        self.assertFalse(
            bool(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        )
        self.assertIs(self.window._chat_session, session)

    def test_target_messages_are_runtime_only_and_close_releases_session(self) -> None:
        message = TwitchMessage(
            "Viewer",
            "runtime only message",
            datetime.now(timezone.utc),
            message_id="message-1",
            broadcaster_user_id="target-id",
        )
        self.sessions[0].message_received.emit(message)
        self.application.processEvents()
        self.assertIn("runtime only message", self.window.chat_view.toPlainText())

        self.window.close()
        self.application.processEvents()

        self.assertEqual(self.sessions[0].closed, 1)
        self.assertEqual(self.window.chat_view.history.entries, ())

    def test_repeated_create_toggle_and_close_cycles_are_safe(self) -> None:
        self.window.shutdown()
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

        for cycle in range(25):
            session = _FakeChatSession(f"target-{cycle}")
            window = RaidLandingWindow(
                _candidate(),
                Mock(),
                chat_session_factory=lambda _target, _parent, current=session: current,
            )
            window.show()
            window.always_on_top_checkbox.setChecked(True)
            window.always_on_top_checkbox.setChecked(False)
            window.close()
            window.deleteLater()
            self.application.processEvents()
            self.assertEqual(session.started, 1)
            self.assertEqual(session.closed, 1)


if __name__ == "__main__":
    unittest.main()
