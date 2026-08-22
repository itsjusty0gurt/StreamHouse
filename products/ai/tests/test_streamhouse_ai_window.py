from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QBoxLayout

from shared.streamhouse_runtime.qt_settings import AI_APPLICATION_NAME
from products.ai.streamhouse_ai.app import StreamhouseAIWindow


class StreamhouseAIWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get("STREAMHOUSE_DATA_DIR")
        os.environ["STREAMHOUSE_DATA_DIR"] = self.temporary_directory.name
        window_settings = QSettings(
            os.path.join(self.temporary_directory.name, "window.ini"),
            QSettings.Format.IniFormat,
        )
        self.window = StreamhouseAIWindow(
            port=0,
            window_settings=window_settings,
        )

    def tearDown(self) -> None:
        self.window.close()
        if self.old_data_dir is None:
            os.environ.pop("STREAMHOUSE_DATA_DIR", None)
        else:
            os.environ["STREAMHOUSE_DATA_DIR"] = self.old_data_dir
        self.temporary_directory.cleanup()

    def test_ai_subpages_are_left_navigation_pages(self) -> None:
        self.assertEqual(self.window.windowTitle(), AI_APPLICATION_NAME)
        self.assertEqual(tuple(self.window.navigation_buttons), self.window.NAVIGATION)
        for name in self.window.NAVIGATION:
            self.window._show_page(name)
            self.assertIs(self.window.page_stack.currentWidget(), self.window.pages[name])
            self.assertTrue(self.window.navigation_buttons[name].isChecked())

    def test_personality_and_model_are_saved_by_streamhouse_ai(self) -> None:
        self.window._show_page("Settings")
        self.window.model_edit.setText("qwen3:8b")
        self.window._save_ai_settings()
        self.window._show_page("Personality")
        self.window.personality_edit.setPlainText("Dry and concise.")
        self.window._save_ai_settings()
        self.assertEqual(self.window.reasoning_service.settings.model, "qwen3:8b")
        self.assertEqual(
            self.window.reasoning_service.settings.personality,
            "Dry and concise.",
        )

    def test_discarded_pre_alpha_settings_schema_is_rejected(self) -> None:
        path = self.window.settings_store.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"_version":1,"model":"old"}', encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "discarded pre-alpha schema"):
            self.window.settings_store.load()

    def test_portrait_mode_moves_navigation_to_the_top(self) -> None:
        self.window._apply_responsive_layout(True)
        self.assertEqual(
            self.window.shell_layout.direction(),
            QBoxLayout.Direction.TopToBottom,
        )
        self.assertEqual(
            self.window.navigation_layout.direction(),
            QBoxLayout.Direction.LeftToRight,
        )
        self.window._apply_responsive_layout(False)
        self.assertEqual(
            self.window.shell_layout.direction(),
            QBoxLayout.Direction.LeftToRight,
        )


if __name__ == "__main__":
    unittest.main()
