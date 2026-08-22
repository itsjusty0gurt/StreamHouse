from __future__ import annotations

import unittest

from products.ai.streamhouse_ai.windows_presence import WindowsHubPresenceNotifier
from shared.streamhouse_shared.presence_protocol import (
    HUB_WINDOW_TITLE,
)


class _FakeUser32:
    def __init__(self) -> None:
        self.window = 42
        self.available_title = HUB_WINDOW_TITLE
        self.searched_titles: list[str] = []
        self.messages: list[tuple[int, int, int, int]] = []

    def FindWindowW(self, _class_name, window_title) -> int:
        self.searched_titles.append(window_title)
        return self.window if window_title == self.available_title else 0

    def IsWindow(self, window: int) -> bool:
        return window == self.window and bool(window)

    def PostMessageW(
        self,
        window: int,
        message: int,
        w_param: int,
        l_param: int,
    ) -> bool:
        self.messages.append((window, message, w_param, l_param))
        return True


class WindowsHubPresenceNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user32 = _FakeUser32()
        self.notifier = WindowsHubPresenceNotifier.__new__(
            WindowsHubPresenceNotifier
        )
        self.notifier.port = 8765
        self.notifier.protocol_version = 2
        self.notifier.connected = False
        self.notifier._window_handle = 0
        self.notifier._message_id = 50000
        self.notifier._user32 = self.user32

    def test_ai_announces_and_disconnects_through_window_messages(self) -> None:
        self.assertTrue(self.notifier.refresh())
        self.assertEqual(
            self.user32.messages[-1],
            (42, 50000, 2, 8765),
        )

        self.notifier.disconnect()
        self.assertEqual(
            self.user32.messages[-1],
            (42, 50000, 2, 0),
        )
        self.assertFalse(self.notifier.connected)

if __name__ == "__main__":
    unittest.main()
