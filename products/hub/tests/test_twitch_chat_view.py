import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QUrl
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication

from products.hub.twitch.models import TwitchMessage
from products.hub.ui.structured_twitch_chat_view import TwitchChatView


class TwitchChatViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_rendered_message_emits_context_metadata(self) -> None:
        view = TwitchChatView()
        view.append(
            "<div class='chat-message'><a class='chat-context-target' "
            "href='streamhouse-chat-context://message?user_id=viewer-1&amp;"
            "user_name=Viewer&amp;message_id=message-1'></a>Hello</div>"
        )
        received = []
        view.chatter_context_requested.connect(
            lambda user_id, user_name, message_id: received.append(
                (user_id, user_name, message_id)
            )
        )
        request = Mock()
        request.linkUrl.return_value = QUrl(
            "streamhouse-chat-context://message?user_id=viewer-1&"
            "user_name=Viewer&message_id=message-1"
        )
        view.lastContextMenuRequest = Mock(return_value=request)
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            QPoint(25, 25),
            QPoint(25, 25),
        )

        view.contextMenuEvent(event)

        self.assertEqual(received, [("viewer-1", "Viewer", "message-1")])
        self.assertIn("chat-context-target", view.toHtml())
        request.setAccepted.assert_called_once_with(True)
        self.assertTrue(event.isAccepted())
        view.close()

    def test_append_updates_loaded_page_without_full_render(self) -> None:
        view = TwitchChatView()
        view._loaded = True
        view._render = Mock()
        view._append_html = Mock()

        view.append("<div>new message</div>")

        view._append_html.assert_called_once_with("<div>new message</div>")
        view._render.assert_not_called()
        self.assertIn("new message", view.toHtml())
        view.close()

    def test_new_messages_stay_pinned_when_reader_is_at_bottom(self) -> None:
        view = TwitchChatView()
        view._loaded = True
        view.page().runJavaScript = Mock()

        view._append_html("<div>new message</div>")

        self.assertTrue(view.pinned_to_latest)
        self.assertEqual(view.pending_message_count, 0)
        self.assertTrue(view.jump_to_latest_button.isHidden())
        view.close()

    def test_scrolled_reader_is_not_moved_and_can_jump_to_latest(self) -> None:
        view = TwitchChatView()
        view._loaded = True
        view.page().runJavaScript = Mock()
        view._handle_bottom_state(False)

        view._append_html("<div>first message</div>")
        view._append_html("<div>second message</div>")

        self.assertFalse(view.pinned_to_latest)
        self.assertEqual(view.pending_message_count, 2)
        self.assertIn("2 new messages", view.jump_to_latest_button.text())
        self.assertFalse(view.jump_to_latest_button.isHidden())

        view.jump_to_latest()
        self.assertTrue(view.pinned_to_latest)
        self.assertEqual(view.pending_message_count, 0)
        self.assertTrue(view.jump_to_latest_button.isHidden())
        view.close()

    def test_manual_return_to_bottom_clears_pending_indicator(self) -> None:
        view = TwitchChatView()
        view._loaded = True
        view.page().runJavaScript = Mock()
        view._handle_bottom_state(False)
        view._append_html("<div>message</div>")

        view._handle_bottom_state(True)

        self.assertTrue(view.pinned_to_latest)
        self.assertEqual(view.pending_message_count, 0)
        self.assertTrue(view.jump_to_latest_button.isHidden())
        view.close()

    def test_scroll_bridge_and_resize_preserve_intentional_unpinned_state(self) -> None:
        view = TwitchChatView()
        view._loaded = True
        view.page().runJavaScript = Mock()
        view._scroll_bridge.report_bottom_state(False)
        QApplication.processEvents()
        view._append_html("<div>message</div>")

        view.resize(640, 360)
        QApplication.processEvents()

        self.assertFalse(view.pinned_to_latest)
        self.assertEqual(view.pending_message_count, 1)
        self.assertFalse(view.jump_to_latest_button.isHidden())
        view.close()

    def test_structured_messages_are_bounded_and_context_resolves_entry(self) -> None:
        view = TwitchChatView(history_limit=2)
        for number in range(3):
            view.append_message(
                TwitchMessage(
                    username="Viewer",
                    text=f"Message {number}",
                    received_at=datetime.now(timezone.utc),
                    message_id=f"message-{number}",
                    user_id="viewer-1",
                )
            )

        self.assertEqual(len(view.history.entries), 2)
        self.assertNotIn("Message 0", view.toPlainText())
        self.assertIn("Message 2", view.toPlainText())
        self.assertIn("chat-message", view.toHtml())
        self.assertNotIn("border-radius", view.toHtml())
        view.close()

    def test_moderation_removes_message_and_user_entries_without_placeholders(self) -> None:
        view = TwitchChatView()
        view.append_message(
            TwitchMessage(
                "Viewer",
                "remove one",
                datetime.now(timezone.utc),
                message_id="message-1",
                user_id="viewer-1",
            )
        )
        view.append_message(
            TwitchMessage(
                "Viewer",
                "keep same name",
                datetime.now(timezone.utc),
                message_id="message-2",
                user_id="viewer-2",
            )
        )
        view.append_message(
            TwitchMessage(
                "VIEWER",
                "remove by stable user",
                datetime.now(timezone.utc),
                message_id="message-3",
                user_id="viewer-1",
            )
        )

        self.assertTrue(view.remove_message("message-1"))
        self.assertFalse(view.remove_message("unknown"))
        self.assertEqual(view.remove_user_messages("viewer-1"), 1)
        self.assertEqual(view.remove_user_messages("unknown"), 0)

        self.assertNotIn("remove one", view.toPlainText())
        self.assertNotIn("remove by stable user", view.toPlainText())
        self.assertIn("keep same name", view.toPlainText())
        self.assertNotIn("[message deleted]", view.toPlainText())
        view.close()

    def test_moderation_removal_does_not_force_scrolled_reader_to_bottom(self) -> None:
        view = TwitchChatView()
        view.append_message(
            TwitchMessage(
                "Viewer",
                "remove me",
                datetime.now(timezone.utc),
                message_id="message-1",
                user_id="viewer-1",
            )
        )
        view._loaded = True
        view.page().runJavaScript = Mock()
        view._handle_bottom_state(False)
        view._append_html("<div>pending</div>")

        self.assertTrue(view.remove_message("message-1"))

        self.assertFalse(view.pinned_to_latest)
        self.assertEqual(view.pending_message_count, 1)
        self.assertFalse(view.jump_to_latest_button.isHidden())
        view.close()

    def test_optimistic_message_mark_does_not_rerender_or_force_bottom(self) -> None:
        view = TwitchChatView()
        view.append_message(
            TwitchMessage(
                "Viewer",
                "delete me",
                datetime.now(timezone.utc),
                message_id="message-1",
                user_id="viewer-1",
            )
        )
        view._loaded = True
        view._render = Mock()
        view.page().runJavaScript = Mock()
        view._handle_bottom_state(False)

        self.assertTrue(view.mark_deleted("message-1"))

        view._render.assert_not_called()
        self.assertFalse(view.pinned_to_latest)
        self.assertIn("[message deleted]", view.toPlainText())
        view.close()

    def test_full_clear_is_idempotent_and_leaves_empty_history(self) -> None:
        view = TwitchChatView()
        view.append_message(
            TwitchMessage(
                "Viewer",
                "hello",
                datetime.now(timezone.utc),
                message_id="message-1",
                user_id="viewer-1",
            )
        )

        view.clear()
        view.clear()

        self.assertEqual(view.history.entries, ())
        self.assertEqual(view.toPlainText(), "")
        self.assertTrue(view.pinned_to_latest)
        self.assertEqual(view.pending_message_count, 0)
        self.assertTrue(view.jump_to_latest_button.isHidden())
        view.close()


if __name__ == "__main__":
    unittest.main()
