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
    QSplitter,
    QWidget,
)

from core.window_state import WindowStateStore


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


if __name__ == "__main__":
    unittest.main()
