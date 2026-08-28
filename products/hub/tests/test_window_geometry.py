import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QRect, Qt, Signal
from PySide6.QtWidgets import QApplication, QMainWindow

from products.hub.core.window_geometry import (
    WindowGeometryController,
    clamp_frame_geometry,
    fit_window_to_screen,
    screen_for_frame,
)


class GeometryTests(unittest.TestCase):
    def test_work_area_clamping(self):
        area = QRect(3440, 0, 1920, 1040)
        cases = (
            (QRect(3500, 50, 900, 700), QRect(3500, 50, 900, 700)),
            (QRect(3500, 50, 2800, 700), QRect(3440, 50, 1920, 700)),
            (QRect(3500, 50, 900, 1080), QRect(3500, 0, 900, 1040)),
            (QRect(4900, 50, 900, 700), QRect(4460, 50, 900, 700)),
            (QRect(3500, 700, 900, 700), QRect(3500, 340, 900, 700)),
            (QRect(-4000, -3000, 900, 700), QRect(3440, 0, 900, 700)),
        )
        for frame, expected in cases:
            with self.subTest(frame=frame):
                self.assertEqual(clamp_frame_geometry(frame, area), expected)

    def test_negative_origin_and_left_top_taskbars(self):
        area = QRect(-1880, -1060, 1880, 1060)
        self.assertEqual(
            clamp_frame_geometry(QRect(-1920, -1080, 1920, 1080), area), area
        )

    def test_repeated_monitor_moves_never_grow_or_progressively_shrink(self):
        small = QRect(3440, 0, 1920, 1040)
        large = QRect(0, 0, 3440, 1040)
        frame = QRect(3450, 50, 3000, 900)
        for _ in range(10):
            frame = clamp_frame_geometry(frame, small)
            self.assertEqual(frame.size().width(), 1920)
            self.assertEqual(frame.size().height(), 900)
            frame = clamp_frame_geometry(frame, large)
            self.assertEqual(frame.size().width(), 1920)
            self.assertEqual(frame, clamp_frame_geometry(frame, large))

    def test_mixed_dpi_uses_logical_work_area_without_rescaling(self):
        # The same physical 1920x1080 panel at 100%, 125% and 150%. Qt has
        # already converted these work areas; do not multiply sizes by DPR.
        for width, height in ((1920, 1040), (1536, 832), (1280, 693)):
            with self.subTest(width=width):
                area = QRect(-width, 0, width, height)
                frame = clamp_frame_geometry(QRect(-width, 0, 2800, 1000), area)
                self.assertEqual(frame, QRect(-width, 0, width, min(1000, height)))


class FakeScreen(QObject):
    availableGeometryChanged = Signal(QRect)
    geometryChanged = Signal(QRect)
    logicalDotsPerInchChanged = Signal(float)

    def __init__(self, area):
        super().__init__()
        self.area = area

    def availableGeometry(self):
        return QRect(self.area)


class WindowGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QMainWindow()
        self.window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.window.setGeometry(30, 40, 600, 400)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_screen_selection_overlap_and_disconnected_monitor(self):
        left = FakeScreen(QRect(-3440, 0, 3440, 1040))
        right = FakeScreen(QRect(0, 0, 1920, 1040))
        with patch("products.hub.core.window_geometry.QGuiApplication") as app:
            app.screens.return_value = [left, right]
            app.primaryScreen.return_value = right
            self.assertIs(screen_for_frame(QRect(-2500, 0, 2400, 900)), left)
            self.assertIs(screen_for_frame(QRect(-100, 0, 1000, 900)), right)
            self.assertIs(screen_for_frame(QRect(9000, 0, 1000, 900)), right)
            app.screens.return_value = []
            self.assertIsNone(screen_for_frame(QRect(0, 0, 1000, 900)))

    def test_valid_geometry_unchanged_and_fonts_untouched(self):
        original = self.window.geometry()
        font = self.window.font()
        fit_window_to_screen(self.window, FakeScreen(QRect(0, 0, 1920, 1040)))
        self.assertEqual(self.window.geometry(), original)
        self.assertEqual(self.window.font(), font)

    def test_client_size_accounts_for_native_decorations_exactly_once(self):
        window = Mock()
        window.isMaximized.return_value = False
        window.isFullScreen.return_value = False
        window.isMinimized.return_value = False
        window.geometry.return_value = QRect(3458, 51, 2800, 1000)
        window.frameGeometry.return_value = QRect(3450, 20, 2816, 1039)
        def applied(x, y, width, height):
            window.geometry.return_value = QRect(x, y, width, height)
            window.frameGeometry.return_value = QRect(x - 8, y - 31, width + 16, height + 39)
        window.setGeometry.side_effect = applied
        area = FakeScreen(QRect(3440, 0, 1920, 1040))
        fit_window_to_screen(window, area)
        window.setGeometry.assert_called_once_with(3448, 32, 1904, 1000)
        # Corrected native frame is already inside the area: do not subtract
        # the frame margins from its client size on subsequent corrections.
        window.frameGeometry.return_value = QRect(3440, 1, 1920, 1039)
        window.reset_mock()
        fit_window_to_screen(window, area)
        window.setGeometry.assert_not_called()

    def test_native_frame_fits_including_titlebar(self):
        self.window.hide()
        self.window.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        self.window.show()
        self.app.processEvents()
        area = QRect(0, 0, 500, 350)
        fit_window_to_screen(self.window, FakeScreen(area))
        self.assertTrue(area.contains(self.window.frameGeometry()))
        frame = self.window.frameGeometry()
        fit_window_to_screen(self.window, FakeScreen(area))
        self.assertEqual(self.window.frameGeometry(), frame)

    def test_maximized_fullscreen_minimized_are_left_to_qt(self):
        area = FakeScreen(QRect(0, 0, 400, 300))
        for show in (self.window.showMaximized, self.window.showFullScreen,
                     self.window.showMinimized):
            show()
            self.app.processEvents()
            before = self.window.geometry()
            state = self.window.windowState()
            fit_window_to_screen(self.window, area)
            self.assertEqual(self.window.geometry(), before)
            self.assertEqual(self.window.windowState(), state)
            self.window.showNormal()

    def controller_on(self, screen):
        controller = WindowGeometryController(self.window)
        # Mock the OS screen handle only. Exercise real signals, the queued
        # controller, and real QWidget geometry with synthetic monitor areas.
        controller._attach_handle = Mock()
        controller._screen_changed(screen)
        return controller

    def test_destination_screen_is_authoritative_and_old_signals_disconnected(self):
        source = FakeScreen(QRect(0, 0, 3440, 1040))
        target = FakeScreen(QRect(3440, 0, 1920, 1040))
        controller = self.controller_on(source)
        with patch("products.hub.core.window_geometry.QGuiApplication.screens",
                   return_value=[source, target]):
            self.window.setGeometry(3500, 50, 2800, 900)
            controller._screen_changed(target)
            self.app.processEvents()
            self.assertTrue(target.area.contains(self.window.frameGeometry()))
            self.assertEqual(self.window.width(), 1920)
            self.assertFalse(controller._pending)
            source.availableGeometryChanged.emit(source.area)
            self.assertFalse(controller._pending)
            target.area = QRect(3440, 40, 1920, 960)
            target.availableGeometryChanged.emit(target.area)
            self.app.processEvents()
            self.assertTrue(target.area.contains(self.window.frameGeometry()))

    def test_correction_waits_until_native_drag_finishes(self):
        target = FakeScreen(QRect(0, 0, 500, 350))
        controller = self.controller_on(target)
        with patch("products.hub.core.window_geometry.QGuiApplication.screens",
                   return_value=[target]):
            before = self.window.geometry()
            controller.begin_interactive_move()
            controller.request_fit()
            self.app.processEvents()
            self.assertEqual(self.window.geometry(), before)
            controller.end_interactive_move()
            self.app.processEvents()
            self.assertTrue(target.area.contains(self.window.frameGeometry()))

    def test_user_moves_and_resizes_on_same_monitor_are_not_intercepted(self):
        controller = self.controller_on(FakeScreen(QRect(0, 0, 1920, 1040)))
        controller._fit_timer.stop()
        controller._pending = False
        controller.begin_interactive_move()
        self.window.setGeometry(50, 60, 640, 430)
        controller.end_interactive_move()
        self.app.processEvents()
        self.assertEqual(self.window.geometry(), QRect(50, 60, 640, 430))
        self.assertFalse(controller._pending)

    def test_restore_from_maximized_fits_normal_geometry(self):
        screen = FakeScreen(QRect(0, 0, 500, 350))
        controller = self.controller_on(screen)
        with patch("products.hub.core.window_geometry.QGuiApplication.screens",
                   return_value=[screen]):
            self.window.showMaximized()
            self.app.processEvents()
            self.assertTrue(self.window.isMaximized())
            self.window.showNormal()
            self.app.processEvents()
            self.assertFalse(self.window.isMaximized())
            self.assertTrue(screen.area.contains(self.window.frameGeometry()))

    def test_show_attaches_qwindow_and_schedules_initial_fit(self):
        controller = WindowGeometryController(self.window)
        self.window.hide()
        self.window.show()
        self.app.processEvents()
        self.assertIs(controller._handle, self.window.windowHandle())
        self.assertIs(controller._screen, self.window.windowHandle().screen())
        self.assertTrue(controller._screen.availableGeometry().contains(
            self.window.frameGeometry()))

    def test_closing_cancels_pending_fit(self):
        controller = WindowGeometryController(self.window)
        controller.request_fit()
        self.window.close()
        self.assertFalse(controller._fit_timer.isActive())


if __name__ == "__main__":
    unittest.main()
