"""Shared frameless window chrome for Streamhouse desktop applications."""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


TITLE_BAR_HEIGHT = 34
RESIZE_BORDER_WIDTH = 5


class StreamhouseTitleBar(QWidget):
    """Product-neutral title bar with standard desktop window controls."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._drag_offset: QPoint | None = None
        self.setObjectName("streamhouseTitleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(7)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("streamhouseTitleBarIcon")
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("streamhouseTitleBarText")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        self.minimize_button = self._make_button(
            "streamhouseMinimizeButton", "—", "Minimize"
        )
        self.maximize_button = self._make_button(
            "streamhouseMaximizeButton", "□", "Maximize"
        )
        self.close_button = self._make_button(
            "streamhouseCloseButton", "×", "Close"
        )
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

        self.setStyleSheet(
            """
            QWidget#streamhouseTitleBar {
                background: palette(window);
                border: none;
                border-bottom: 1px solid palette(mid);
            }
            QLabel#streamhouseTitleBarText {
                color: palette(window-text);
                font-weight: 600;
            }
            QLabel#streamhouseTitleBarIcon { border: none; }
            QToolButton {
                background: transparent;
                color: palette(window-text);
                border: none;
                border-radius: 0;
                font-size: 16px;
                padding: 0;
            }
            QToolButton:hover { background: palette(midlight); }
            QToolButton:pressed { background: palette(mid); }
            QToolButton#streamhouseCloseButton:hover {
                background: #c42b1c;
                color: white;
            }
            QToolButton#streamhouseCloseButton:pressed {
                background: #9b1c13;
                color: white;
            }
            """
        )
        window.installEventFilter(self)
        self._sync_from_window()

    def _make_button(self, name: str, text: str, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(name)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(46, TITLE_BAR_HEIGHT)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _sync_from_window(self) -> None:
        self.title_label.setText(self._window.windowTitle())
        icon = self._window.windowIcon()
        if icon.isNull():
            self.icon_label.clear()
        else:
            self.icon_label.setPixmap(icon.pixmap(QSize(16, 16)))
        maximized = self._window.isMaximized()
        self.maximize_button.setText("❐" if maximized else "□")
        action = "Restore" if maximized else "Maximize"
        self.maximize_button.setToolTip(action)
        self.maximize_button.setAccessibleName(action)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        window = getattr(self, "_window", None)
        if watched is window and event.type() in (
            QEvent.Type.WindowTitleChange,
            QEvent.Type.WindowIconChange,
            QEvent.Type.WindowStateChange,
        ):
            self._sync_from_window()
        return super().eventFilter(watched, event)

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        handle = self._window.windowHandle()
        if handle is not None and handle.startSystemMove():
            event.accept()
            return
        self._drag_offset = (
            event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
        )
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._window.isMaximized()
        ):
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class _ResizeHandle(QWidget):
    def __init__(self, window: QMainWindow, edges: Qt.Edges) -> None:
        super().__init__(window)
        self._window = window
        self._edges = edges
        self.setObjectName("streamhouseResizeHandle")
        self.setCursor(_resize_cursor_for_edges(edges))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._window.isMaximized()
        ):
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemResize(self._edges):
                event.accept()
                return
        super().mousePressEvent(event)


def _resize_cursor_for_edges(edges: Qt.Edges) -> QCursor:
    left = bool(edges & Qt.Edge.LeftEdge)
    right = bool(edges & Qt.Edge.RightEdge)
    top = bool(edges & Qt.Edge.TopEdge)
    bottom = bool(edges & Qt.Edge.BottomEdge)

    if (left and top) or (right and bottom):
        shape = Qt.CursorShape.SizeFDiagCursor
    elif (right and top) or (left and bottom):
        shape = Qt.CursorShape.SizeBDiagCursor
    elif left or right:
        shape = Qt.CursorShape.SizeHorCursor
    elif top or bottom:
        shape = Qt.CursorShape.SizeVerCursor
    else:
        shape = Qt.CursorShape.ArrowCursor
    return QCursor(shape)


class WindowChrome(QObject):
    """Owns the title bar and invisible native resize handles for one window."""

    def __init__(
        self,
        window: QMainWindow,
        title_bar: StreamhouseTitleBar,
        *,
        native_frame: bool = False,
    ) -> None:
        super().__init__(window)
        self._window = window
        self.title_bar = title_bar
        self._native_frame = native_frame
        left = Qt.Edge.LeftEdge
        right = Qt.Edge.RightEdge
        top = Qt.Edge.TopEdge
        bottom = Qt.Edge.BottomEdge
        self._handles = {
            "left": _ResizeHandle(window, left),
            "right": _ResizeHandle(window, right),
            "top": _ResizeHandle(window, top),
            "bottom": _ResizeHandle(window, bottom),
            "top_left": _ResizeHandle(window, left | top),
            "top_right": _ResizeHandle(window, right | top),
            "bottom_left": _ResizeHandle(window, left | bottom),
            "bottom_right": _ResizeHandle(window, right | bottom),
        }
        window.installEventFilter(self)
        self._layout_handles()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        window = getattr(self, "_window", None)
        if watched is window and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.WindowStateChange,
        ):
            self._layout_handles()
        return super().eventFilter(watched, event)

    def _layout_handles(self) -> None:
        window = self._window
        width = window.width()
        height = window.height()
        border = RESIZE_BORDER_WIDTH
        corner = border * 2
        maximized = window.isMaximized()
        geometries = {
            "left": QRect(0, corner, border, max(0, height - 2 * corner)),
            "right": QRect(width - border, corner, border, max(0, height - 2 * corner)),
            "top": QRect(corner, 0, max(0, width - 2 * corner), border),
            "bottom": QRect(corner, height - border, max(0, width - 2 * corner), border),
            "top_left": QRect(0, 0, corner, corner),
            "top_right": QRect(width - corner, 0, corner, corner),
            "bottom_left": QRect(0, height - corner, corner, corner),
            "bottom_right": QRect(width - corner, height - corner, corner, corner),
        }
        for name, handle in self._handles.items():
            handle.setVisible(not self._native_frame and not maximized)
            handle.setGeometry(geometries[name])
            handle.raise_()

def install_window_chrome(
    window: QMainWindow,
    *,
    native_windows_frame: bool = False,
) -> WindowChrome:
    """Install the standard Streamhouse title bar on a main window once."""

    existing = getattr(window, "_streamhouse_window_chrome", None)
    if isinstance(existing, WindowChrome):
        return existing

    original_central_widget = window.takeCentralWidget()
    shell = QWidget(window)
    shell.setObjectName("streamhouseWindowShell")
    layout = QVBoxLayout(shell)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    title_bar = StreamhouseTitleBar(window)
    layout.addWidget(title_bar)
    if original_central_widget is not None:
        layout.addWidget(original_central_widget, 1)
    window.setCentralWidget(shell)

    menu_bar = window.menuBar()
    if menu_bar is not None and not menu_bar.actions():
        menu_bar.hide()
    use_native_frame = native_windows_frame and sys.platform == "win32"
    if use_native_frame:
        title_bar.hide()
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        window.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        window.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        window.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        window.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
    else:
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

    chrome = WindowChrome(window, title_bar, native_frame=use_native_frame)
    window._streamhouse_window_chrome = chrome
    return chrome
