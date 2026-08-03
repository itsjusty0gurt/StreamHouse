from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QWidget

from shared.streamhouse_ui import StreamhouseTitleBar, install_window_chrome


class WindowChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.content = QLabel("application content")
        self.window.setCentralWidget(self.content)
        self.window.setWindowTitle("Streamhouse Test")

    def tearDown(self) -> None:
        self.window.close()
        self.application.processEvents()

    def test_install_wraps_content_and_enables_frameless_window(self) -> None:
        chrome = install_window_chrome(self.window)

        self.assertIsInstance(chrome.title_bar, StreamhouseTitleBar)
        self.assertTrue(
            self.window.windowFlags() & Qt.WindowType.FramelessWindowHint
        )
        self.assertIs(self.content.parentWidget(), self.window.centralWidget())
        self.assertEqual(chrome.title_bar.title_label.text(), "Streamhouse Test")

    def test_install_is_idempotent(self) -> None:
        first = install_window_chrome(self.window)
        second = install_window_chrome(self.window)

        self.assertIs(first, second)
        title_bars = self.window.findChildren(StreamhouseTitleBar)
        self.assertEqual(len(title_bars), 1)

    def test_title_and_icon_follow_window_metadata(self) -> None:
        chrome = install_window_chrome(self.window)
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor("#00d084"))

        self.window.setWindowTitle("Renamed Product")
        self.window.setWindowIcon(QIcon(pixmap))
        self.application.processEvents()

        self.assertEqual(chrome.title_bar.title_label.text(), "Renamed Product")
        self.assertFalse(chrome.title_bar.icon_label.pixmap().isNull())

    def test_maximize_control_toggles_window_state(self) -> None:
        chrome = install_window_chrome(self.window)
        self.window.show()
        self.application.processEvents()

        chrome.title_bar.toggle_maximized()
        self.application.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.assertEqual(chrome.title_bar.maximize_button.toolTip(), "Restore")

        chrome.title_bar.toggle_maximized()
        self.application.processEvents()
        self.assertFalse(self.window.isMaximized())
        self.assertEqual(chrome.title_bar.maximize_button.toolTip(), "Maximize")

    def test_minimize_and_close_controls_drive_the_window(self) -> None:
        chrome = install_window_chrome(self.window)
        self.window.show()
        self.application.processEvents()

        chrome.title_bar.minimize_button.click()
        self.application.processEvents()
        self.assertTrue(self.window.isMinimized())

        self.window.showNormal()
        chrome.title_bar.close_button.click()
        self.application.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_resize_handles_use_matching_cursor_shapes(self) -> None:
        chrome = install_window_chrome(self.window)

        handles = {
            name: handle.cursor().shape()
            for name, handle in chrome._handles.items()
            if isinstance(handle, QWidget)
        }

        self.assertEqual(handles["left"], Qt.CursorShape.SizeHorCursor)
        self.assertEqual(handles["right"], Qt.CursorShape.SizeHorCursor)
        self.assertEqual(handles["top"], Qt.CursorShape.SizeVerCursor)
        self.assertEqual(handles["bottom"], Qt.CursorShape.SizeVerCursor)
        self.assertEqual(handles["top_left"], Qt.CursorShape.SizeFDiagCursor)
        self.assertEqual(handles["bottom_right"], Qt.CursorShape.SizeFDiagCursor)
        self.assertEqual(handles["top_right"], Qt.CursorShape.SizeBDiagCursor)
        self.assertEqual(handles["bottom_left"], Qt.CursorShape.SizeBDiagCursor)

    @unittest.skipUnless(sys.platform == "win32", "Windows native frame only")
    def test_native_windows_frame_preserves_system_snap_capabilities(self) -> None:
        chrome = install_window_chrome(
            self.window,
            native_windows_frame=True,
        )
        flags = self.window.windowFlags()

        self.assertFalse(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowSystemMenuHint)
        self.assertTrue(flags & Qt.WindowType.WindowMinimizeButtonHint)
        self.assertTrue(flags & Qt.WindowType.WindowMaximizeButtonHint)
        self.assertTrue(flags & Qt.WindowType.WindowCloseButtonHint)
        self.assertTrue(chrome.title_bar.isHidden())
        self.assertTrue(chrome._native_frame)


if __name__ == "__main__":
    unittest.main()
