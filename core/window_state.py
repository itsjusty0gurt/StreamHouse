from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QMainWindow, QSplitter


class WindowStateStore:
    """Persist and restore main-window geometry and dock placement."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings()

    def restore(self, window: QMainWindow) -> bool:
        geometry = self.settings.value("window/main_geometry")
        restored = isinstance(geometry, QByteArray) and window.restoreGeometry(
            geometry
        )
        state = self.settings.value("window/main_state")
        if isinstance(state, QByteArray):
            window.restoreState(state)
        if restored and not self._intersects_a_screen(window):
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                window.move(available.topLeft())
        splitter = window.findChild(QSplitter, "twitchChannelSplitter")
        splitter_state = self.settings.value("window/companion_splitter")
        if splitter is not None and isinstance(splitter_state, QByteArray):
            splitter.restoreState(splitter_state)
        memories_splitter = window.findChild(QSplitter, "memoriesSplitter")
        memories_state = self.settings.value("window/memories_splitter")
        if memories_splitter is not None and isinstance(
            memories_state,
            QByteArray,
        ):
            memories_splitter.restoreState(memories_state)
        activity_filter = window.findChild(QComboBox, "activityFilterCombo")
        saved_filter = self.settings.value("window/activity_filter", "")
        if activity_filter is not None and saved_filter:
            activity_filter.setCurrentText(str(saved_filter))
        return restored

    def save(self, window: QMainWindow) -> None:
        self.settings.setValue("window/main_geometry", window.saveGeometry())
        self.settings.setValue("window/main_state", window.saveState())
        splitter = window.findChild(QSplitter, "twitchChannelSplitter")
        if splitter is not None:
            self.settings.setValue(
                "window/companion_splitter",
                splitter.saveState(),
            )
        memories_splitter = window.findChild(QSplitter, "memoriesSplitter")
        if memories_splitter is not None:
            self.settings.setValue(
                "window/memories_splitter",
                memories_splitter.saveState(),
            )
        activity_filter = window.findChild(QComboBox, "activityFilterCombo")
        if activity_filter is not None:
            self.settings.setValue(
                "window/activity_filter",
                activity_filter.currentText(),
            )
        self.settings.sync()

    @staticmethod
    def _intersects_a_screen(window: QMainWindow) -> bool:
        frame = window.frameGeometry()
        return any(
            frame.intersects(screen.availableGeometry())
            for screen in QGuiApplication.screens()
        )
