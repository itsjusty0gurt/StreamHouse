from __future__ import annotations

import unittest

from sally_companion.windows_presence import WindowsBotPresenceNotifier


class _FakeUser32:
    def __init__(self) -> None:
        self.window = 42
        self.messages: list[tuple[int, int, int, int]] = []

    def FindWindowW(self, _class_name, _window_title) -> int:
        return self.window

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


class WindowsBotPresenceNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user32 = _FakeUser32()
        self.notifier = WindowsBotPresenceNotifier.__new__(
            WindowsBotPresenceNotifier
        )
        self.notifier.port = 8765
        self.notifier.protocol_version = 1
        self.notifier.connected = False
        self.notifier._window_handle = 0
        self.notifier._message_id = 50000
        self.notifier._user32 = self.user32

    def test_companion_announces_and_disconnects_through_window_messages(self) -> None:
        self.assertTrue(self.notifier.refresh())
        self.assertEqual(
            self.user32.messages[-1],
            (42, 50000, 1, 8765),
        )

        self.notifier.disconnect()
        self.assertEqual(
            self.user32.messages[-1],
            (42, 50000, 1, 0),
        )
        self.assertFalse(self.notifier.connected)


if __name__ == "__main__":
    unittest.main()
