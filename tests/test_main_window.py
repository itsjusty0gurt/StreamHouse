import logging
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.logger import Logger
from core.settings import AppSettings
from ui.main_window import MainWindow


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.original_logger = Logger._logger
        Logger._logger = logging.Logger("SallyUItest", logging.DEBUG)

        self.settings_patch = patch(
            "ui.main_window.SettingsStore.load",
            return_value=AppSettings(),
        )
        self.settings_patch.start()
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        self.application.processEvents()
        self.settings_patch.stop()
        Logger._logger = self.original_logger

    def test_navigation_selects_one_button_and_correct_page(self) -> None:
        cases = (
            (self.window.ui.dashboardButton, self.window.ui.dashboardPage),
            (self.window.ui.twitchButton, self.window.ui.twitchPage),
            (self.window.ui.logsButton, self.window.ui.logsPage),
            (self.window.ui.settingsButton, self.window.ui.settingsPage),
        )
        buttons = [button for button, _ in cases]

        for button, page in cases:
            button.click()

            self.assertIs(self.window.ui.mainStack.currentWidget(), page)
            self.assertTrue(button.isChecked())
            self.assertEqual(
                sum(candidate.isChecked() for candidate in buttons),
                1,
            )
            self.assertEqual(self.window.statusBar().currentMessage(), "")

    def test_log_buttons_feed_the_ui(self) -> None:
        self.window.ui.testInfoButton.click()
        self.window.ui.testWarningButton.click()
        self.window.ui.testErrorButton.click()
        self.application.processEvents()

        output = self.window.ui.logOutput.toPlainText()
        self.assertIn("Developer test information message.", output)
        self.assertIn("Developer test warning message.", output)
        self.assertIn("Developer test error message.", output)

    def test_settings_are_applied_to_controls(self) -> None:
        settings = AppSettings(
            startup_page="Logs",
            log_level="WARNING",
            ui_log_limit=500,
            show_developer_tools=False,
        )

        self.window._settings_to_controls(settings)
        self.window._apply_settings(settings)

        self.assertEqual(self.window.ui.startupPageCombo.currentText(), "Logs")
        self.assertEqual(self.window.ui.logLevelCombo.currentText(), "WARNING")
        self.assertEqual(self.window.ui.logOutput.maximumBlockCount(), 500)
        self.assertTrue(self.window.ui.testInfoButton.isHidden())


if __name__ == "__main__":
    unittest.main()
