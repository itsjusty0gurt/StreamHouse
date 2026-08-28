"""Fit Hub's normal window to its current work area, in Qt logical coordinates."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QTimer
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QMainWindow


def clamp_frame_geometry(frame: QRect, available: QRect) -> QRect:
    """Keep the whole frame visible without expanding or repeatedly shrinking it."""
    if available.isEmpty():
        return QRect(frame)
    width = min(frame.width(), available.width())
    height = min(frame.height(), available.height())
    return QRect(
        max(available.x(), min(frame.x(), available.x() + available.width() - width)),
        max(available.y(), min(frame.y(), available.y() + available.height() - height)),
        width,
        height,
    )


def screen_for_frame(frame: QRect) -> QScreen | None:
    """Choose the most-overlapped display for restoration, or the primary screen."""
    screens = QGuiApplication.screens()
    if not screens:
        return None

    def overlap(screen: QScreen) -> int:
        intersection = frame.intersected(screen.availableGeometry())
        return max(0, intersection.width()) * max(0, intersection.height())

    best = max(screens, key=overlap)
    return best if overlap(best) else QGuiApplication.primaryScreen()


def fit_window_to_screen(window: QMainWindow, screen: QScreen | None = None) -> None:
    """Clamp a normal window; native Qt/Windows owns all other window states."""
    if window.isMaximized() or window.isFullScreen() or window.isMinimized():
        return
    frame = window.frameGeometry()
    screen = screen or screen_for_frame(frame)
    if screen is None:
        return
    # Qt can finalize decoration metrics on the first resize after a state/DPI
    # change. One bounded recheck accounts for that, without a retry timer or
    # repeatedly deducting decorations from an already-correct client size.
    for _ in range(2):
        frame = window.frameGeometry()
        target = clamp_frame_geometry(frame, screen.availableGeometry())
        if target == frame:
            return
        # setGeometry takes CLIENT coordinates. Apply position and size together
        # so a queued native move from a preceding resize cannot restore the old
        # offscreen position (notably when returning from maximized).
        client = window.geometry()
        window.setGeometry(
            target.x() + client.x() - frame.x(),
            target.y() + client.y() - frame.y(),
            max(1, target.width() - (frame.width() - client.width())),
            max(1, target.height() - (frame.height() - client.height())),
        )


class WindowGeometryController(QObject):
    """One event-driven geometry policy; no polling or resize/move interception.

    Qt supplies the destination screen and work area. Native Windows move-loop
    notifications only defer a pending fit until the user finishes dragging;
    they never replace native frame, maximize, hit-testing or DPI handling.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._handle = None
        self._screen = None
        self._screen_connections = []
        self._interactive_move = False
        self._pending = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_pending)
        window.installEventFilter(self)
        QGuiApplication.instance().screenRemoved.connect(self.request_fit)

    def request_fit(self, *_args) -> None:
        self._pending = True
        if not self._interactive_move:
            # Coalesce screen/DPI/state notifications and let Qt settle native
            # frame metrics before comparing frameGeometry. Not a polling timer.
            self._fit_timer.start(0)

    def begin_interactive_move(self) -> None:
        self._interactive_move = True
        self._fit_timer.stop()

    def end_interactive_move(self) -> None:
        self._interactive_move = False
        if self._pending:
            self.request_fit()

    def _watch_screen(self, screen: QScreen | None) -> None:
        if screen is self._screen:
            return
        for connection in self._screen_connections:
            QObject.disconnect(connection)
        self._screen_connections.clear()
        self._screen = screen
        if screen is not None:
            self._screen_connections = [
                screen.availableGeometryChanged.connect(self.request_fit),
                screen.geometryChanged.connect(self.request_fit),
                screen.logicalDotsPerInchChanged.connect(self.request_fit),
            ]

    def _screen_changed(self, screen: QScreen | None) -> None:
        self._watch_screen(screen)
        self.request_fit()

    def _attach_handle(self) -> None:
        handle = self._window.windowHandle()
        if handle is not None and handle is not self._handle:
            self._handle = handle
            handle.screenChanged.connect(self._screen_changed)
        if handle is not None:
            self._watch_screen(handle.screen())

    def _fit_pending(self) -> None:
        if self._interactive_move or not self._window.isVisible():
            return
        self._pending = False
        self._attach_handle()
        screen = self._screen
        if screen not in QGuiApplication.screens():
            screen = screen_for_frame(self._window.frameGeometry())
        fit_window_to_screen(self._window, screen)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._window:
            if event.type() == QEvent.Type.Show:
                self._attach_handle()
                self.request_fit()
            elif event.type() in (
                QEvent.Type.WindowStateChange,
                QEvent.Type.DevicePixelRatioChange,
            ):
                self.request_fit()
            elif event.type() == QEvent.Type.Hide:
                self._fit_timer.stop()
        return super().eventFilter(watched, event)
