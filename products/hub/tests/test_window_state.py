import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
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
from shared.streamhouse_runtime.qt_settings import migrate_qsettings_values


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

    def test_legacy_qt_values_copy_without_overwriting_streamhouse_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = QSettings(
                str(root / "legacy.ini"),
                QSettings.Format.IniFormat,
            )
            destination = QSettings(
                str(root / "streamhouse.ini"),
                QSettings.Format.IniFormat,
            )
            legacy.setValue("window/main_geometry", b"legacy-geometry")
            legacy.setValue("window/layout_mode", "portrait")
            destination.setValue("window/layout_mode", "landscape")

            copied = migrate_qsettings_values(destination, legacy)
            copied_again = migrate_qsettings_values(destination, legacy)

            self.assertEqual(copied, 1)
            self.assertEqual(copied_again, 0)
            self.assertEqual(
                destination.value("window/main_geometry"),
                b"legacy-geometry",
            )
            self.assertEqual(
                destination.value("window/layout_mode"),
                "landscape",
            )

    def test_companion_layout_round_trip(self) -> None:
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
