from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QMainWindow,
    QSplitter,
    QTabWidget,
    QWidget,
)

from products.hub.core.window_geometry import fit_window_to_screen


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
        if restored:
            fit_window_to_screen(window)
        splitter = window.findChild(QSplitter, "twitchChannelSplitter")
        splitter_state = self.settings.value("window/channel_splitter")
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
        automation_page = window.findChild(QWidget, "automationPage")
        routine_sort_button = window.findChild(
            QAbstractButton,
            "automationRoutineSortButton",
        )
        if routine_sort_button is not None:
            routine_sort_button.setChecked(
                self.settings.value(
                    "window/automation_routine_alphabetical",
                    False,
                    type=bool,
                )
            )
        routine_id = str(
            self.settings.value("window/automation_selected_routine", "") or ""
        )
        if automation_page is not None and routine_id and hasattr(
            automation_page, "select_routine"
        ):
            automation_page.select_routine(routine_id)
        automation_splitter = window.findChild(QSplitter, "automationSplitter")
        automation_splitter_state = self.settings.value(
            "window/automation_splitter"
        )
        if automation_splitter is not None and isinstance(
            automation_splitter_state, QByteArray
        ):
            automation_splitter.restoreState(automation_splitter_state)
        for object_name, key in (
            ("automationTabs", "window/automation_tab"),
            ("automationEditorTabs", "window/automation_editor_tab"),
        ):
            tabs = window.findChild(QTabWidget, object_name)
            if tabs is not None:
                index = int(self.settings.value(key, tabs.currentIndex()))
                tabs.setCurrentIndex(max(0, min(index, tabs.count() - 1)))
        return restored

    def save(self, window: QMainWindow) -> None:
        self.settings.setValue("window/main_geometry", window.saveGeometry())
        self.settings.setValue("window/main_state", window.saveState())
        splitter = window.findChild(QSplitter, "twitchChannelSplitter")
        if splitter is not None:
            self.settings.setValue(
                "window/channel_splitter",
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
        automation_page = window.findChild(QWidget, "automationPage")
        if automation_page is not None:
            self.settings.setValue(
                "window/automation_selected_routine",
                str(automation_page.property("selectedRoutineId") or ""),
            )
        routine_sort_button = window.findChild(
            QAbstractButton,
            "automationRoutineSortButton",
        )
        if routine_sort_button is not None:
            self.settings.setValue(
                "window/automation_routine_alphabetical",
                routine_sort_button.isChecked(),
            )
        automation_splitter = window.findChild(QSplitter, "automationSplitter")
        if automation_splitter is not None:
            self.settings.setValue(
                "window/automation_splitter",
                automation_splitter.saveState(),
            )
        for object_name, key in (
            ("automationTabs", "window/automation_tab"),
            ("automationEditorTabs", "window/automation_editor_tab"),
        ):
            tabs = window.findChild(QTabWidget, object_name)
            if tabs is not None:
                self.settings.setValue(key, tabs.currentIndex())
        self.settings.sync()
