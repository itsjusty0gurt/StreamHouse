import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QUrl
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication

from ui.twitch_chat_view import TwitchChatView


class TwitchChatViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_rendered_message_emits_context_metadata(self) -> None:
        view = TwitchChatView()
        view.append(
            "<div class='chat-message'><a class='chat-context-target' "
            "href='sally-chat-context://message?user_id=viewer-1&amp;"
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
            "sally-chat-context://message?user_id=viewer-1&"
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


if __name__ == "__main__":
    unittest.main()
