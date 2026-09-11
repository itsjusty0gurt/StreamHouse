import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from products.hub.ui.twitch_chat_input import (
    TWITCH_SLASH_COMMANDS,
    TwitchChatInputController,
    TwitchSlashActionWorker,
    TwitchUserSuggestion,
    parse_twitch_slash_request,
)


class TwitchChatInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.edit = QLineEdit()
        self.edit.resize(420, 32)
        self.edit.show()
        self.controller = TwitchChatInputController(
            self.edit,
            lambda: (
                TwitchUserSuggestion("y0gurtviewer", "Y0gurtViewer"),
                TwitchUserSuggestion("yoshi123", "Yoshi"),
                TwitchUserSuggestion("someoneelse", "Someone Else"),
            ),
            history_limit=3,
        )

    def tearDown(self) -> None:
        self.controller.popup.close()
        self.edit.close()

    def _type(self, text: str) -> None:
        self.edit.clear()
        QTest.keyClicks(self.edit, text)
        QApplication.processEvents()

    def test_slash_opens_and_filters_supported_api_actions(self) -> None:
        self._type("/")
        self.assertTrue(self.controller.helper_visible)
        self.assertEqual(self.controller.suggestions.count(), 3)

        self._type("/BA")
        self.assertEqual(self.controller.suggestions.count(), 1)
        self.assertIn("/ban <user>", self.controller.suggestions.item(0).text())
        advertised = {command.name for command in TWITCH_SLASH_COMMANDS}
        self.assertEqual(advertised, {"ban", "timeout", "unban"})
        self.assertNotIn("clear", advertised)
        self.assertNotIn("mod", advertised)

    def test_keyboard_completion_never_executes_an_action(self) -> None:
        self._type("/ti")
        QTest.keyClick(self.edit, Qt.Key.Key_Tab)

        self.assertEqual(self.edit.text(), "/timeout ")
        self.assertTrue(self.controller.helper_visible)
        self.assertEqual(self.controller.suggestions.count(), 3)

        QTest.keyClick(self.edit, Qt.Key.Key_Down)
        QTest.keyClick(self.edit, Qt.Key.Key_Return)
        self.assertEqual(self.edit.text(), "/timeout y0gurtviewer ")
        self.assertFalse(self.controller.helper_visible)

    def test_escape_dismisses_without_clearing_draft(self) -> None:
        self._type("/ba")
        QTest.keyClick(self.edit, Qt.Key.Key_Escape)
        self.assertFalse(self.controller.helper_visible)
        self.assertEqual(self.edit.text(), "/ba")

    def test_username_suggestions_are_local_partial_and_case_insensitive(self) -> None:
        self._type("/ban Y")
        labels = [
            self.controller.suggestions.item(index).text()
            for index in range(self.controller.suggestions.count())
        ]
        self.assertEqual(len(labels), 2)
        self.assertTrue(any("y0gurtviewer" in label for label in labels))
        self.assertTrue(any("yoshi123" in label for label in labels))

    def test_sent_history_walks_backward_forward_and_preserves_composing_text(self) -> None:
        for message in ("hello", "!discord", "thanks for the raid"):
            self.controller.record_sent(message)
        self.edit.clear()

        QTest.keyClick(self.edit, Qt.Key.Key_Up)
        self.assertEqual(self.edit.text(), "thanks for the raid")
        QTest.keyClick(self.edit, Qt.Key.Key_Up)
        self.assertEqual(self.edit.text(), "!discord")
        QTest.keyClick(self.edit, Qt.Key.Key_Up)
        self.assertEqual(self.edit.text(), "hello")
        QTest.keyClick(self.edit, Qt.Key.Key_Down)
        self.assertEqual(self.edit.text(), "!discord")
        QTest.keyClick(self.edit, Qt.Key.Key_Down)
        QTest.keyClick(self.edit, Qt.Key.Key_Down)
        self.assertEqual(self.edit.text(), "")

        self.edit.setText("unfinished draft")
        QTest.keyClick(self.edit, Qt.Key.Key_Up)
        self.assertEqual(self.edit.text(), "unfinished draft")

    def test_history_is_bounded_and_collapses_consecutive_duplicates(self) -> None:
        for message in ("one", "one", "two", "three", "four"):
            self.controller.record_sent(message)
        self.assertEqual(tuple(self.controller.history), ("two", "three", "four"))

    def test_helper_navigation_takes_precedence_over_history(self) -> None:
        self.controller.record_sent("ordinary message")
        self._type("/")
        QTest.keyClick(self.edit, Qt.Key.Key_Down)
        self.assertEqual(self.edit.text(), "/")
        self.assertEqual(self.controller.suggestions.currentRow(), 1)


class TwitchSlashRequestTests(unittest.TestCase):
    def test_parser_supports_existing_api_backed_actions(self) -> None:
        timeout = parse_twitch_slash_request("/timeout @viewer 30 spam links")
        self.assertEqual(timeout.action, "timeout")
        self.assertEqual(timeout.user_reference, "viewer")
        self.assertEqual(timeout.duration, 30)
        self.assertEqual(timeout.reason, "spam links")
        self.assertEqual(
            parse_twitch_slash_request("/timeout viewer").duration,
            600,
        )
        self.assertEqual(
            parse_twitch_slash_request("/ban viewer spam").reason,
            "spam",
        )

    def test_parser_rejects_unknown_missing_user_and_invalid_duration(self) -> None:
        for text in ("/clear", "/ban", "/timeout viewer 9999999"):
            with self.assertRaises(ValueError):
                parse_twitch_slash_request(text)

    def test_worker_resolves_user_then_reuses_moderation_service(self) -> None:
        service = Mock()
        service.resolve_user.return_value = {"id": "viewer-1"}
        service.moderate_user.return_value = True
        request = parse_twitch_slash_request("/timeout viewer 45 reason")
        worker = TwitchSlashActionWorker(service, request)
        finished = []
        worker.signals.finished.connect(lambda *args: finished.append(args))

        worker.run()

        service.resolve_user.assert_called_once_with("viewer")
        service.moderate_user.assert_called_once_with(
            "timeout",
            "viewer-1",
            duration=45,
            reason="reason",
        )
        self.assertEqual(
            finished,
            [(worker, request, True, "viewer", "")],
        )


if __name__ == "__main__":
    unittest.main()
