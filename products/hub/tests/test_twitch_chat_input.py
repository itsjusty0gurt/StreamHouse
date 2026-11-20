import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QWidget

from products.hub.automation.variable_registry import (
    CallbackVariableProvider,
    VariableAvailability,
    VariableDataType,
    VariableDefinition,
    VariableRegistry,
)
from products.hub.ui.twitch_chat_input import (
    TWITCH_SLASH_COMMANDS,
    TwitchChatInputController,
    TwitchSlashActionWorker,
    TwitchUserSuggestion,
    install_chat_variable_picker,
    is_chat_variable,
    is_twitch_action_message,
    parse_twitch_slash_request,
    render_chat_message,
)
from products.hub.ui.variable_picker import VariablePickerButton


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
        self.assertEqual(self.controller.suggestions.count(), 22)

        self._type("/BA")
        self.assertEqual(self.controller.suggestions.count(), 1)
        self.assertIn("/ban <user>", self.controller.suggestions.item(0).text())
        advertised = {command.name for command in TWITCH_SLASH_COMMANDS}
        self.assertEqual(
            advertised,
            {
                "ban", "timeout", "unban", "clear", "slow", "slowoff",
                "followers", "followersoff", "subscribers", "subscribersoff",
                "emoteonly", "emoteonlyoff", "uniquechat", "uniquechatoff",
                "mod", "unmod", "vip", "unvip", "raid", "unraid", "announce",
                "shoutout",
            },
        )
        self.assertNotIn("color", advertised)
        timeout = next(item for item in TWITCH_SLASH_COMMANDS if item.name == "timeout")
        self.assertEqual(timeout.syntax, "/timeout <user> <duration> [reason]")
        vip = next(item for item in TWITCH_SLASH_COMMANDS if item.name == "vip")
        self.assertEqual(vip.syntax, "/vip <user>")

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


class TwitchChatVariableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.values = {
            "hub.uptime": "01:23:45",
            "custom.greeting": "hello",
            "obs.current_scene": "Gameplay",
        }
        definitions = tuple(
            VariableDefinition(
                name,
                name,
                f"Test {name} value.",
                VariableDataType.TEXT,
                "Test",
                name.split(".", 1)[0].title(),
            )
            for name in self.values
        ) + (
            VariableDefinition(
                "user.display_name",
                "User Display Name",
                "Triggering user's display name.",
                VariableDataType.TEXT,
                "Twitch Context",
                "User",
                availability=VariableAvailability.CONTEXTUAL,
                required_context=("user.display_name",),
            ),
            VariableDefinition(
                "automation.result",
                "Automation Result",
                "Routine-scoped task output.",
                VariableDataType.TEXT,
                "Automation",
                "Automation",
                availability=VariableAvailability.TEMPORARY,
            ),
        )

        def resolve(name, context):
            if name in self.values:
                return True, self.values[name], ""
            if name == "user.display_name" and name in context:
                return True, context[name], ""
            return False, None, "Unavailable without routine context."

        self.registry = VariableRegistry()
        self.registry.register(
            CallbackVariableProvider("Test", definitions, resolve)
        )

    def test_render_resolves_current_global_values_and_keeps_missing_tokens(self) -> None:
        template = (
            "{custom.greeting} {hub.uptime} / {hub.uptime} "
            "in {obs.current_scene}; {missing.variable}"
        )
        self.assertEqual(
            render_chat_message(self.registry, template),
            "hello 01:23:45 / 01:23:45 in Gameplay; {missing.variable}",
        )

        self.values["hub.uptime"] = "01:24:00"
        self.assertEqual(
            render_chat_message(self.registry, "Live for {hub.uptime}"),
            "Live for 01:24:00",
        )

    def test_render_does_not_use_contextual_or_temporary_values(self) -> None:
        self.assertEqual(
            render_chat_message(
                self.registry,
                "Hi {user.display_name}; result {automation.result}",
            ),
            "Hi {user.display_name}; result {automation.result}",
        )

    def test_chat_picker_reuses_shared_button_and_filters_non_global_values(self) -> None:
        parent = QWidget()
        layout = QHBoxLayout(parent)
        edit = QLineEdit("Say now", parent)
        layout.addWidget(edit)
        layout.addWidget(QWidget(parent))
        edit.setSelection(4, 3)
        picker = install_chat_variable_picker(
            self.registry,
            edit,
            layout,
            parent,
        )
        try:
            self.assertIsInstance(picker, VariablePickerButton)
            self.assertIs(layout.itemAt(1).widget(), picker)
            self.assertTrue(
                is_chat_variable(self.registry.definition("hub.uptime"))
            )
            self.assertFalse(
                is_chat_variable(self.registry.definition("user.display_name"))
            )
            self.assertFalse(
                is_chat_variable(self.registry.definition("automation.result"))
            )
            picker.insert_placeholder("{hub.uptime}")
            self.assertEqual(edit.text(), "Say {hub.uptime}")
        finally:
            parent.close()

    def test_twitch_action_detection_preserves_me_without_weakening_slash_commands(self) -> None:
        self.assertTrue(is_twitch_action_message("/me hello"))
        self.assertTrue(is_twitch_action_message("  /ME hello"))
        self.assertFalse(is_twitch_action_message("/ban viewer"))
        self.assertFalse(is_twitch_action_message("!command"))


class TwitchSlashRequestTests(unittest.TestCase):
    def test_parser_supports_all_api_backed_actions(self) -> None:
        cases = (
            ("/ban SomeUser spam links", "ban", "SomeUser", None, "spam links", ""),
            ("/timeout @viewer 10m caps", "timeout", "viewer", 600, "caps", ""),
            ("/unban viewer", "unban", "viewer", None, "", ""),
            ("/clear", "clear", "", None, "", ""),
            ("/slow 30s", "slow", "", 30, "", ""),
            ("/slowoff", "slowoff", "", None, "", ""),
            ("/followers", "followers", "", 0, "", ""),
            ("/followers 2h", "followers", "", 120, "", ""),
            ("/followersoff", "followersoff", "", None, "", ""),
            ("/subscribers", "subscribers", "", None, "", ""),
            ("/subscribersoff", "subscribersoff", "", None, "", ""),
            ("/emoteonly", "emoteonly", "", None, "", ""),
            ("/emoteonlyoff", "emoteonlyoff", "", None, "", ""),
            ("/uniquechat", "uniquechat", "", None, "", ""),
            ("/uniquechatoff", "uniquechatoff", "", None, "", ""),
            ("/mod viewer", "mod", "viewer", None, "", ""),
            ("/unmod viewer", "unmod", "viewer", None, "", ""),
            ("/vip viewer", "vip", "viewer", None, "", ""),
            ("/unvip viewer", "unvip", "viewer", None, "", ""),
            ("/raid channel", "raid", "channel", None, "", ""),
            ("/unraid", "unraid", "", None, "", ""),
            ("/announce Stream starts now!", "announce", "", None, "", "Stream starts now!"),
            ("/shoutout @SomeUser", "shoutout", "SomeUser", None, "", ""),
        )
        for text, action, user, duration, reason, message in cases:
            with self.subTest(text=text):
                request = parse_twitch_slash_request(text)
                self.assertEqual(request.action, action)
                self.assertEqual(request.user_reference, user)
                self.assertEqual(request.duration, duration)
                self.assertEqual(request.reason, reason)
                self.assertEqual(request.message, message)

        self.assertEqual(
            parse_twitch_slash_request("/TiMeOuT viewer 2h reason").duration,
            7200,
        )

    def test_parser_rejects_unknown_missing_user_and_invalid_duration(self) -> None:
        for text in (
            "/unknown",
            "/ban",
            "/timeout viewer",
            "/timeout viewer nope",
            "/timeout viewer 9999999",
            "/slow 2",
            "/slow 2h",
            "/followers 30s",
            "/announce",
            "/shoutout",
            "/clear extra",
        ):
            with self.assertRaises(ValueError):
                parse_twitch_slash_request(text)

    def test_worker_routes_through_twitch_service_action_path(self) -> None:
        service = Mock()
        service.execute_slash_action.return_value = (True, "viewer")
        request = parse_twitch_slash_request("/timeout viewer 45 reason")
        worker = TwitchSlashActionWorker(service, request)
        finished = []
        worker.signals.finished.connect(lambda *args: finished.append(args))

        worker.run()

        service.execute_slash_action.assert_called_once_with(request)
        self.assertEqual(
            finished,
            [(worker, request, True, "viewer", "")],
        )


if __name__ == "__main__":
    unittest.main()
