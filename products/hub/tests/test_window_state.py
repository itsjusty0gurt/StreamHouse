import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QRect
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTabWidget,
    QWidget,
)

from products.hub.core.window_state import WindowStateStore


class WindowStateStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_geometry_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(
                str(Path(directory) / "window.ini"),
                QSettings.Format.IniFormat,
            )
            store = WindowStateStore(settings)
            original = QMainWindow()
            original.resize(920, 610)
            original.move(40, 55)
            store.save(original)

            restored = QMainWindow()
            self.assertTrue(store.restore(restored))
            # Qt constrains restored geometry to the synthetic offscreen
            # monitor, but preserves it as closely as that monitor permits.
            self.assertEqual(restored.height(), original.height())
            self.assertGreater(restored.width(), 640)
            self.assertTrue(
                restored.frameGeometry().intersects(
                    self.application.primaryScreen().availableGeometry()
                )
            )

    def test_valid_saved_geometry_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WindowStateStore(QSettings(
                str(Path(directory) / "window.ini"), QSettings.Format.IniFormat))
            original = QMainWindow()
            original.setGeometry(40, 55, 600, 400)
            store.save(original)
            restored = QMainWindow()
            self.assertTrue(store.restore(restored))
            self.assertEqual(restored.geometry(), original.geometry())

    def test_saved_oversized_and_offscreen_geometry_is_fully_contained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WindowStateStore(QSettings(
                str(Path(directory) / "window.ini"), QSettings.Format.IniFormat))
            area = self.application.primaryScreen().availableGeometry()
            for rectangle in (QRect(700, 700, 600, 400),
                              QRect(9000, -3000, 2800, 1600)):
                with self.subTest(rectangle=rectangle):
                    original = QMainWindow()
                    original.setGeometry(rectangle)
                    store.save(original)
                    restored = QMainWindow()
                    self.assertTrue(store.restore(restored))
                    self.assertTrue(area.contains(restored.frameGeometry()))

    def test_partial_intersection_is_not_accepted_as_valid_restoration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WindowStateStore(QSettings(
                str(Path(directory) / "window.ini"), QSettings.Format.IniFormat))
            original = QMainWindow()
            store.save(original)
            restored = QMainWindow()
            area = self.application.primaryScreen().availableGeometry()
            def restore_as_partial(_geometry):
                restored.setGeometry(area.right() - 10, area.bottom() - 10, 600, 400)
                return True
            # Bypass Qt's own saved-position correction to verify Hub's stricter
            # whole-frame correction, rather than merely testing Qt restoration.
            with patch.object(restored, "restoreGeometry", side_effect=restore_as_partial):
                self.assertTrue(store.restore(restored))
            self.assertTrue(area.contains(restored.frameGeometry()))

    def test_channel_layout_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(
                str(Path(directory) / "window.ini"),
                QSettings.Format.IniFormat,
            )
            store = WindowStateStore(settings)
            original = QMainWindow()
            splitter = QSplitter(Qt.Orientation.Horizontal, original)
            splitter.setObjectName("twitchChannelSplitter")
            splitter.addWidget(QWidget())
            splitter.addWidget(QWidget())
            original.setCentralWidget(splitter)
            splitter.setSizes([700, 300])
            activity_filter = QComboBox(original)
            activity_filter.setObjectName("activityFilterCombo")
            activity_filter.addItems(("All activity", "Raids"))
            activity_filter.setCurrentText("Raids")
            store.save(original)

            restored = QMainWindow()
            restored_splitter = QSplitter(
                Qt.Orientation.Horizontal,
                restored,
            )
            restored_splitter.setObjectName("twitchChannelSplitter")
            restored_splitter.addWidget(QWidget())
            restored_splitter.addWidget(QWidget())
            restored.setCentralWidget(restored_splitter)
            restored_filter = QComboBox(restored)
            restored_filter.setObjectName("activityFilterCombo")
            restored_filter.addItems(("All activity", "Raids"))

            store.restore(restored)

            self.assertEqual(restored_filter.currentText(), "Raids")
            self.assertGreater(restored_splitter.sizes()[0], 0)

    def test_automation_workspace_tabs_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(
                str(Path(directory) / "window.ini"),
                QSettings.Format.IniFormat,
            )
            store = WindowStateStore(settings)
            original = QMainWindow()
            page = QWidget(original)
            page.setObjectName("automationPage")
            page.setProperty("selectedRoutineId", "routine-1")
            tabs = QTabWidget(page)
            tabs.setObjectName("automationTabs")
            tabs.addTab(QWidget(), "Routines")
            tabs.addTab(QWidget(), "History")
            tabs.setCurrentIndex(1)
            editor_tabs = QTabWidget(page)
            editor_tabs.setObjectName("automationEditorTabs")
            editor_tabs.addTab(QWidget(), "Triggers")
            editor_tabs.addTab(QWidget(), "Tasks")
            editor_tabs.setCurrentIndex(1)
            sort_button = QPushButton(page)
            sort_button.setObjectName("automationRoutineSortButton")
            sort_button.setCheckable(True)
            sort_button.setChecked(True)
            original.setCentralWidget(page)
            store.save(original)

            restored = QMainWindow()
            restored_page = QWidget(restored)
            restored_page.setObjectName("automationPage")
            restored_tabs = QTabWidget(restored_page)
            restored_tabs.setObjectName("automationTabs")
            restored_tabs.addTab(QWidget(), "Routines")
            restored_tabs.addTab(QWidget(), "History")
            restored_editor_tabs = QTabWidget(restored_page)
            restored_editor_tabs.setObjectName("automationEditorTabs")
            restored_editor_tabs.addTab(QWidget(), "Triggers")
            restored_editor_tabs.addTab(QWidget(), "Tasks")
            restored_sort_button = QPushButton(restored_page)
            restored_sort_button.setObjectName("automationRoutineSortButton")
            restored_sort_button.setCheckable(True)
            restored.setCentralWidget(restored_page)

            store.restore(restored)

            self.assertEqual(restored_tabs.currentIndex(), 1)
            self.assertEqual(restored_editor_tabs.currentIndex(), 1)
            self.assertTrue(restored_sort_button.isChecked())


if __name__ == "__main__":
    unittest.main()
